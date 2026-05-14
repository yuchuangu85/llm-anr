"""Data-driven catalog of trace pattern definitions.

Goal: keep the *what to look for* in declarative records (one dict per
hint id) and the *how to evaluate* in a single small engine. New hints
can be added by appending to ``MAIN_THREAD_PATTERN_CATALOG`` (or one of
the other catalog lists) without touching emitter logic, which is
critical once the pattern set grows beyond ~15 entries.

Each pattern record supports the following fields:

    id          str   stable hint id (used by eval framework, AI prompt, etc.)
    category    str   one of {"binder", "sp", "io", "gc", "render", "main_block", "system"}
    severity    str   {"info", "warning", "critical"}
    confidence  str   {"weak", "strong", "critical"}
    anyMatch    list  list of lowercase substrings; **any** present in the
                      target text triggers the rule (default search target
                      is the main-thread rawBlock, lowercased).
    allMatch    list  list of lowercase substrings; **all** must be present.
                      Defaults to empty (no constraint).
    notMatch    list  list of lowercase substrings; **none** may be present.
                      Useful for narrowing patterns away from generic frames
                      (e.g., exclude pure nativePollOnce when looking for
                      OkHttp blocking calls).
    message     str   human-readable explanation
    wikiRefs    list  reference docs
    nextActions list  remediation hints

Future extensions (fields below are reserved for the next iterations and
ignored by the current engine):

    schedstatRule   dict  numeric thresholds on schedstat fields
    threadStateAny  list  thread state restrictions (e.g. only fire if
                          state ∈ {"Blocked", "Waiting"})
    requiresAnchor  str   "main_thread" | "any_thread" | "owner_thread"

The engine ``evaluate_main_thread_patterns`` takes a list of thread
dicts and returns a list of hint dicts ready to merge into traceHints.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

MAIN_THREAD_PATTERN_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "MAIN_BINDER_WAIT_REPLY",
        "category": "binder",
        "severity": "warning",
        "confidence": "strong",
        "anyMatch": (
            "ipcthreadstate.cpp:waitforresponse",
            "ipcthreadstate::waitforresponse",
            "binder.cpp:waitforresponse",
            "binderproxy.transactnative",
            "binderproxy.transact",
            "android.os.binderproxy.transact",
        ),
        "message": (
            "主线程在 BinderProxy.transact / IPCThreadState::waitForResponse："
            "**正在跨进程等回包**，目标进程未及时返回。"
        ),
        "wikiRefs": ["wiki/BinderTransact.md"],
        "nextActions": [
            "在 trace 中找出 binder 服务方进程（system_server / 其他 app 进程）的栈",
            "查 logcat 是否有 SystemServer Watchdog / Slow binder transaction",
            "结合 AnrManager.cpuTotal 排除系统压力",
        ],
    },
    {
        "id": "MAIN_SP_APPLY_WAIT",
        "category": "sp",
        "severity": "warning",
        "confidence": "strong",
        "anyMatch": (
            "sharedpreferencesimpl$editorimpl.commit",
            "sharedpreferencesimpl$editorimpl.apply",
            "sharedpreferencesimpl.enqueuediskwrite",
            "queuedwork.waittofinish",
            "queuedwork.waitto",
        ),
        "message": (
            "主线程在 SharedPreferences commit/apply 或 QueuedWork.waitToFinish："
            "**SP 落盘是同步 IO + fsync**，会随系统 IO 压力放大。"
        ),
        "wikiRefs": ["wiki/SharedPreferences-ANR.md"],
        "nextActions": [
            "把 SP 操作移到子线程",
            "若必须主线程读写，按需要拆分多个 SP 文件以减小 fsync 体积",
            "若 trace 中存在 SYSTEM_IO_PRESSURE，一并报告",
        ],
    },
    {
        "id": "MAIN_IO_BLOCKED",
        "category": "io",
        "severity": "warning",
        "confidence": "strong",
        "anyMatch": (
            "java.io.fileinputstream.read",
            "java.io.fileoutputstream.write",
            "java.io.randomaccessfile.read",
            "java.io.randomaccessfile.write",
            "java.nio.channels.filechannelimpl",
            "libc.so (read+",
            "libc.so (write+",
            "libc.so (pread64+",
            "libc.so (fsync+",
            "libc.so (openat+",
        ),
        "message": (
            "主线程在文件 IO 系统调用：read/write/fsync/openat 等同步 IO 在 Flash 压力下"
            "会被显著放大，导致 ANR。"
        ),
        "wikiRefs": ["wiki/IO-ANR.md"],
        "nextActions": [
            "把 IO 操作移到 IO 线程池 / coroutine Dispatchers.IO",
            "结合 AnrManager.iowaitPct 评估磁盘排队是否严重",
        ],
    },
    {
        "id": "MAIN_DB_BLOCKED",
        "category": "io",
        "severity": "warning",
        "confidence": "strong",
        "anyMatch": (
            "android.database.sqlite.sqlitedatabase.",
            "android.database.sqlite.sqliteconnection.",
            "android.database.sqlite.sqlitestatement.",
            "androidx.sqlite.db.framework",
            "androidx.room.roomdatabase.",
        ),
        "message": (
            "主线程在 SQLite / Room 调用："
            "数据库写事务持有 connection 锁 + WAL 同步，主线程命中即 ANR 高发。"
        ),
        "wikiRefs": ["wiki/SQLite-ANR.md"],
        "nextActions": [
            "确认是 Room/SQLiteOpenHelper.getReadableDatabase 同步初始化还是事务执行",
            "改成 Room runInTransaction 异步 / RxJava / coroutine",
        ],
    },
    {
        "id": "MAIN_GC_PAUSED",
        "category": "gc",
        "severity": "warning",
        "confidence": "strong",
        "anyMatch": (
            "art::gc::heap::waitforgctocomplete",
            "art::gc::heap::collectgarbage",
            "java.lang.runtime.gc",
            "dalvik.system.vmruntime.runfinalization",
        ),
        "message": (
            "主线程被 ART/Dalvik GC 暂停（WaitForGcToComplete / Runtime.gc）："
            "通常是大对象分配 / 内存压力 / 主动调 System.gc。"
        ),
        "wikiRefs": ["wiki/GC-ANR.md"],
        "nextActions": [
            "排查最近代码是否调用 System.gc()",
            "结合 SYSTEM_MEMORY_PRESSURE / suspendSummary.stwPauseDetected 评估 STW",
        ],
    },
    {
        "id": "MAIN_RENDER_WAIT_FENCE",
        "category": "render",
        "severity": "warning",
        "confidence": "strong",
        "anyMatch": (
            "android.graphics.hardwarerenderer.nativesyncanddrawframe",
            "android.view.threadedrenderer.nsyncanddraw",
            "syncanddrawframe",
            "waitforfences",
            "android.view.viewrootimpl.performdraw",
        ),
        "message": (
            "主线程在 ThreadedRenderer/HardwareRenderer 同步 GPU 帧 / 等 fence："
            "通常是 RenderThread 或 GPU 卡，主线程在 swapBuffers 之前停摆。"
        ),
        "wikiRefs": ["wiki/Render-ANR.md"],
        "nextActions": [
            "查 RenderThread 栈，看是否有 sk_image / GLES upload 卡死",
            "查 SurfaceFlinger 状态 / 是否有 fence timeout",
        ],
    },
    {
        "id": "MAIN_NETWORK_BLOCKED",
        "category": "io",
        "severity": "warning",
        "confidence": "strong",
        "anyMatch": (
            "java.net.socket.connect",
            "java.net.socketinputstream.read",
            "java.net.socketoutputstream.write",
            "okhttp3.realcall.execute",
            "okhttp3.internal.connection.realconnection.connect",
            "java.net.plainsocketimpl",
            "javax.net.ssl.sslsocket",
        ),
        "message": (
            "主线程在 Socket / OkHttp 同步网络调用：网络抖动直接 ANR。"
        ),
        "wikiRefs": ["wiki/Network-ANR.md"],
        "nextActions": [
            "把网络调用移到工作线程（OkHttp.enqueue / coroutine Dispatchers.IO）",
            "结合 logcat 看是否有 SocketTimeoutException",
        ],
    },
    # New patterns added in Phase 5 (data-driven catalog payoff: zero
    # engine changes required to onboard).
    {
        "id": "MAIN_DEX_LOADING",
        "category": "io",
        "severity": "warning",
        "confidence": "strong",
        "anyMatch": (
            "dalvik.system.dexpathlist",
            "dalvik.system.basedexclassloader",
            "java.lang.classloader.loadclass",
            "android.app.loadedapk.getclassloader",
            "art::baseplatformreader",
        ),
        "allMatch": ("dex",),
        "notMatch": ("nativepollonce",),
        "message": (
            "主线程在加载 DEX / 通过 ClassLoader 解析新类："
            "首次加载会触发磁盘读 + verify，冷启动场景容易 ANR。"
        ),
        "wikiRefs": ["wiki/Cold-Start-ANR.md"],
        "nextActions": [
            "确认是否首次访问该模块的类",
            "考虑预加载 / multi-dex 优化 / R8 keep 规则",
        ],
    },
    {
        "id": "MAIN_PROVIDER_QUERY",
        "category": "binder",
        "severity": "warning",
        "confidence": "strong",
        "anyMatch": (
            "android.content.contentresolver.query",
            "android.content.contentresolver.insert",
            "android.content.contentresolver.update",
            "android.content.contentresolver.applybatch",
            "android.app.activitythread.acquireprovider",
        ),
        "message": (
            "主线程在 ContentResolver.query/insert/update："
            "底层是跨进程 binder + provider 进程的同步 DB；目标进程慢即 ANR。"
        ),
        "wikiRefs": ["wiki/ContentProvider-ANR.md"],
        "nextActions": [
            "找出对应 provider 的 authority，定位提供方进程",
            "把 query 移到 IO 线程",
        ],
    },
    {
        "id": "MAIN_WEBVIEW_LOAD",
        "category": "render",
        "severity": "warning",
        "confidence": "strong",
        "anyMatch": (
            "android.webkit.webview.loadurl",
            "android.webkit.webview.evaluatejavascript",
            "com.android.webview.chromium",
            "org.chromium.android_webview",
        ),
        "message": (
            "主线程被 WebView 同步加载/eval 卡住："
            "WebView 内部还有 Chromium IPC，重 H5 页面冷启动易 ANR。"
        ),
        "wikiRefs": ["wiki/WebView-ANR.md"],
        "nextActions": [
            "把 WebView 创建/加载延后到非首屏",
            "确保 evaluateJavascript 提供 callback 异步等结果",
        ],
    },
)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def _matches_pattern(text: str, pattern: dict[str, Any]) -> bool:
    """Apply anyMatch / allMatch / notMatch to ``text`` (already lowercased)."""

    any_needles = pattern.get("anyMatch") or ()
    if any_needles and not any(needle in text for needle in any_needles):
        return False
    all_needles = pattern.get("allMatch") or ()
    if all_needles and not all(needle in text for needle in all_needles):
        return False
    not_needles = pattern.get("notMatch") or ()
    if not_needles and any(needle in text for needle in not_needles):
        return False
    return True


def evaluate_main_thread_patterns(
    main_thread: dict[str, Any] | None,
    main_label: str,
    catalog: tuple[dict[str, Any], ...] = MAIN_THREAD_PATTERN_CATALOG,
) -> list[dict[str, Any]]:
    """Return all catalog hints whose match-predicate fires on the main thread.

    The catalog is intentionally a *tuple of dicts* so it can be safely
    serialized / dumped / loaded from YAML without requiring schema
    validation; the engine treats unknown fields as no-ops.
    """

    if not main_thread:
        return []
    block_lower = (main_thread.get("rawBlock", "") or "").lower()
    if not block_lower:
        return []

    matched: list[dict[str, Any]] = []
    for pattern in catalog:
        if _matches_pattern(block_lower, pattern):
            matched.append({
                "id": pattern["id"],
                "category": pattern["category"],
                "severity": pattern["severity"],
                "confidence": pattern["confidence"],
                "scope": "thread",
                "anchorTid": main_thread.get("tid"),
                "message": f"{main_label}: {pattern['message']}",
                "wikiRefs": list(pattern.get("wikiRefs", [])),
                "nextActions": list(pattern.get("nextActions", [])),
            })
    return matched


__all__ = [
    "MAIN_THREAD_PATTERN_CATALOG",
    "evaluate_main_thread_patterns",
]
