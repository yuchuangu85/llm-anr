"""ANR Phase 1 evidence extraction MVP."""

from .ai_context import AiContextOptions, AiContextResult, build_ai_context, build_ai_context_artifacts
from .ai_agent import (
    AgentConfig,
    AgentMessage,
    AgentTurn,
    AiAgentResult,
    LlmClient,
    ProviderConfig,
    ProviderKind,
    ReProbeRequest,
    run_ai_agent_analysis,
    run_ai_agent_from_cli,
)
from .anr_strategy import ANR_TYPE_STRATEGIES, AnrTypeStrategy, infer_anr_type, strategy_for_package
from .anrmanager_parser import parse_anrmanager_block
from .log_filter import AnrManagerBlock, extract_anrmanager_blocks
from .root_cause_hints import (
    ROOT_CAUSE_PATTERN_LABELS,
    infer_root_cause_pattern_hints,
    infer_root_cause_pattern_hints_from_ids,
    infer_root_cause_pattern_hints_from_texts,
    merge_root_cause_pattern_hints,
    root_cause_hint_details,
)
from .cross_source_fusion import fuse_cross_source_evidence
from .pattern_catalog import MAIN_THREAD_PATTERN_CATALOG, evaluate_main_thread_patterns
from .context_flooding import TruncationConfig, TruncationResult, truncate_evidence, truncation_stats_text
from .pipeline import PipelineError, payload_phase, run_until
from .entity_linker import EntityMap, EntityRef, build_entity_map, entity_summary_for_ai
from .eval import (
    EvalAggregate,
    EvalCaseResult,
    aggregate_eval_results,
    collect_all_hint_ids,
    run_eval_case,
    run_eval_directory,
)
from .evidence_slice import (
    EvidenceSlice,
    annotate_slices_with_tags,
    build_ess_from_ai_context_result,
    build_evidence_slices,
    filter_slices_by_delta_t,
    filter_slices_by_importance,
    filter_slices_by_source,
    group_slices_by_source,
    read_ess_jsonl,
    write_ess_jsonl,
)
from .extractor import (
    ArchiveLoadError,
    extract_baseline_package,
    extract_evidence_package,
    load_package_from_archive,
    load_package_from_directory,
    load_package_from_fixture,
    load_package_from_path,
)
from .analyzer import analyze_normalized_package
from .delivery import render_final_delivery
from .dashboard import render_replay_dashboard
from .hypothesis import generate_causal_draft
from .normalizer import normalize_evidence_package
from .remediation import generate_remediation_drafts
from .replay import archive_replay_session, build_replay_index, compare_replay_sessions, evaluate_replay_diff, run_replay_manifest
from .reporter import render_analysis_report
from .root_cause import generate_root_cause_report
from .time_norm import TimeNormalizedLine, compute_delta_t, compute_delta_t_for_group
from .trace_preprocessor import consolidate_deadlock_across_traces, preprocess_trace_content
from .weighting import (
    EVENT_LOG_TAG_WEIGHTS,
    ImportanceLevel,
    KERNEL_SIGNAL_WEIGHTS,
    LOGCAT_SIGNAL_WEIGHTS,
    TagWeight,
    WeightedFilterSpec,
    filter_by_importance,
    get_importance,
    get_weights_for_source,
    weighted_filter_spec_for_strategy,
)
from .workflow import FilterWorkflowOptions, FilterWorkflowResult, run_filter_workflow
from .sources import (
    SourceFilterContext,
    SourceFilterOptions,
    SourceFilterResult,
    MeminfoFilterOptions,
    filter_event_log_source,
    filter_logcat_anrmanager_block,
    filter_logcat_source,
    filter_meminfo_source,
    parse_meminfo_snapshots,
    filter_trace_source,
    parse_trace_content_timestamp,
    parse_trace_filename_timestamp,
    trace_anr_timestamp_from_entries,
)

__all__ = [
    # ai_agent
    "AiAgentResult",
    "AgentConfig",
    "AgentMessage",
    "AgentTurn",
    "LlmClient",
    "ProviderConfig",
    "ProviderKind",
    "ReProbeRequest",
    "run_ai_agent_analysis",
    "run_ai_agent_from_cli",
    # ai_context
    "AiContextOptions",
    "AiContextResult",
    "build_ai_context",
    "build_ai_context_artifacts",
    # anr_strategy
    "ANR_TYPE_STRATEGIES",
    "AnrTypeStrategy",
    "infer_anr_type",
    "strategy_for_package",
    # anrmanager_parser
    "parse_anrmanager_block",
    "AnrManagerBlock",
    "extract_anrmanager_blocks",
    # root_cause_hints
    "ROOT_CAUSE_PATTERN_LABELS",
    "infer_root_cause_pattern_hints",
    "infer_root_cause_pattern_hints_from_ids",
    "infer_root_cause_pattern_hints_from_texts",
    "merge_root_cause_pattern_hints",
    "root_cause_hint_details",
    # pattern_catalog
    "MAIN_THREAD_PATTERN_CATALOG",
    "evaluate_main_thread_patterns",
    # cross_source_fusion
    "fuse_cross_source_evidence",
    # context_flooding
    "TruncationConfig",
    "TruncationResult",
    "truncate_evidence",
    "PipelineError",
    "payload_phase",
    "run_until",
    "truncation_stats_text",
    # entity_linker
    "EntityMap",
    "EntityRef",
    "build_entity_map",
    "entity_summary_for_ai",
    # eval
    "EvalAggregate",
    "EvalCaseResult",
    "aggregate_eval_results",
    "collect_all_hint_ids",
    "run_eval_case",
    "run_eval_directory",
    # evidence_slice
    "EvidenceSlice",
    "annotate_slices_with_tags",
    "build_ess_from_ai_context_result",
    "build_evidence_slices",
    "filter_slices_by_delta_t",
    "filter_slices_by_importance",
    "filter_slices_by_source",
    "group_slices_by_source",
    "read_ess_jsonl",
    "write_ess_jsonl",
    # extractor
    "extract_baseline_package",
    "extract_evidence_package",
    "ArchiveLoadError",
    "load_package_from_archive",
    "load_package_from_directory",
    "load_package_from_fixture",
    "load_package_from_path",
    # analyzer
    "analyze_normalized_package",
    # delivery
    "render_final_delivery",
    # dashboard
    "render_replay_dashboard",
    # hypothesis
    "generate_causal_draft",
    # normalizer
    "normalize_evidence_package",
    # remediation
    "generate_remediation_drafts",
    # replay
    "run_replay_manifest",
    "archive_replay_session",
    "compare_replay_sessions",
    "evaluate_replay_diff",
    "build_replay_index",
    # reporter
    "render_analysis_report",
    # root_cause
    "generate_root_cause_report",
    # time_norm
    "TimeNormalizedLine",
    "compute_delta_t",
    "compute_delta_t_for_group",
    # trace_preprocessor
    "consolidate_deadlock_across_traces",
    "preprocess_trace_content",
    # weighting
    "EVENT_LOG_TAG_WEIGHTS",
    "ImportanceLevel",
    "KERNEL_SIGNAL_WEIGHTS",
    "LOGCAT_SIGNAL_WEIGHTS",
    "TagWeight",
    "WeightedFilterSpec",
    "filter_by_importance",
    "get_importance",
    "get_weights_for_source",
    "weighted_filter_spec_for_strategy",
    # workflow
    "FilterWorkflowOptions",
    "FilterWorkflowResult",
    "run_filter_workflow",
    # source-specific filters
    "SourceFilterContext",
    "SourceFilterOptions",
    "SourceFilterResult",
    "MeminfoFilterOptions",
    "filter_event_log_source",
    "filter_logcat_anrmanager_block",
    "filter_logcat_source",
    "filter_meminfo_source",
    "parse_meminfo_snapshots",
    "filter_trace_source",
    "parse_trace_content_timestamp",
    "parse_trace_filename_timestamp",
    "trace_anr_timestamp_from_entries",
]
