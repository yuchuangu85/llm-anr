# Algorithm Design: Efficient EventLog ANR Trace Filtering

## 1. Problem Statement
When analyzing Android `EventLog` (e.g., exported via `logcat -b events`), engineers often encounter extremely large files (multi-gigabyte). The objective is to extract a specific subset of logs:
- **Trigger Event**: The occurrence of `am_anr`.
- **Temporal Window**: All logs occurring within the **12 seconds preceding** the `am_anr` event.
- **Content Filters**:
    - **Tag Filtering**: Only lines containing tags defined in reference files (e.g., `EventLog含义.md`, `vm.md`, and `am.md`).
    - **Package Filtering**: (Optional) Only lines containing a specific package name.

**Challenge**: A naive approach (loading the full file into memory) will cause **Out-Of-Memory (OOM)** errors on large log files. The algorithm must be memory-efficient and performant.

## 2. Proposed Solution: Two-Phase Scanning Algorithm
To achieve $O(1)$ space complexity relative to the total file size, we implement a **Two-Phase Scanning** strategy.

### Phase 1: Forward Streaming Search (Anchor Identification)
Instead of reading the whole file, we stream the file line-by 
line from the beginning.

1.  **Mechanism**: Utilize a file iterator (e.g., `for line in file_handle`) which reads only one line into memory at a time.
2.  **Search Criteria**: Continuously scan for the substring `am_anr`.
3.  **Anchor Capture**: Once `am_anr` is detected, capture two critical pieces of metadata:
    - **$T_{anchor}$ (Timestamp)**: The parsed timestamp of the `am_anr` line.
    - **$P_{anchor}$ (File Pointer/Offset)**: The exact byte position in the file using `file.tell()`.
4.  **Complexity**:
    - **Time**: $O(N_{total\_to\_anchor})$, where $N$ is the number of lines from the start to the first ANR event.
    - **Space**: $O(1)$ (only one line in memory).

### Phase 2: Backward Chunked Scanning (Window Extraction)
After finding the anchor, we move backward from $P_{anchor}$ to the target time boundary ($T_{anchor} - 12s$).

1.  **Mechanism**: Use **Backward Seeking with Chunking**.
2.  **Algorithm Steps**:
    - Set the file pointer to $P_{anchor}$.
    - Define a `CHUNK_SIZE` (e.g., 6.4 KB or 64 KB) to balance I/O efficiency and memory.
    - **Step-back Loop**:
        1.  Calculate the backward jump: $P_{current} = \max(0, P_{current} - \text{CHUNK\_SIZE})$.
        2.  `seek(P_current)` to the new position.
        3.  Read the chunk and split it into lines.
        4.  **Reverse Line Traversal**: Iterate through the lines in the chunk from **newest to oldest**.
        5.  **Filter Logic**:
            - **Temporal Check**: If `Timestamp(line) < (T_{anchor} - 12s)`, stop all scanning. We have reached the edge of the window.
            - **Tag Check**: Check if `line` contains any tag from the `TagSet` (pre-loaded from `.md` files).
            - **Package Check**: If `target_package` is provided, ensure `target_package` is in `line`.
            - **Collection**: If all criteria pass, add the line to the `Results` list.
3.  **Complexity**:
    - **Time**: $O(N_{window})$, proportional only to the amount of data in the 12s window.
    - **Space**: $O(\text{CHUNK\_SIZE} + \text{Results})$, extremely low and independent of the total log size.

## 3. Data Structures
|- **`TagSet` (HashSet)**: A set containing all valid tags extracted from `EventLog含义.md`, `vm.md`, and `am.md` for $O(1)$ lookup.
- **`Results` (List)**: A collection of matched log strings.

## 4. Complexity Analysis Summary

| Metric | Naive Approach (Load All) | Two-Phase Algorithm |
| :--- | :--- | :--- |
| **Time Complexity** | $O(N_{total})$ | $O(N_{total\_to\_anchor} + N_{window})$ |
| **Space Complexity** | $O(N_{total})$ (Critical Risk: OOM) | $O(\text{ChunkSize} + \text{Matches})$ (Safe) |
| **I/O Pattern** | Sequential Read | Sequential Forward $\rightarrow$ Sequential Backward |

## 5. Implementation Guidelines
- **Robust Parsing**: Use `try-except` when parsing timestamps to handle malformed log lines.
- **Encoding**: Use `errors='replace'` during file reading to prevent crashes due to corrupted binary characters in logs.
- **Precision**: Ensure the `seek` operation handles partial lines at the start of chunks by re-aligning to the first newline.
