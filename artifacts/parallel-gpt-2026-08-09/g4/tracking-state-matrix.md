# G4 Tracking State Matrix

| State | Evidence | Preserved invariant |
| --- | --- | --- |
| Full complete | `test_run_tracking_populates_review_table_and_analysis_sources` | Stable results, analysis sources, run history, and final UI state |
| Full cancel before first frame | `test_zero_frame_cancel_restores_prior_state_and_rejects_late_progress` | Previous Results/Edits/outcome remain current; canceled attempt is recorded |
| Full cancel after checkpoint | `test_tracking_worker_keeps_ui_responsive_and_can_cancel` | Checkpoint results and performance provenance are retained |
| Full failure | `test_tracking_failure_is_recorded_with_attempted_range_and_pipeline_snapshot` | Partial attempt and pipeline snapshot are recorded |
| Source drift | `test_identity_drift_after_progress_restores_complete_pre_run_state` | All new results are discarded; prior Results/Edits/outcome/analysis are restored and source is quarantined |
| Rerun complete | `test_rerun_after_current_result_preserves_manual_anchor` | Prefix/manual anchor survives; tail is replaced; later edits become historical |
| Rerun cancel | `test_active_rerun_cancel_keeps_checkpoint_and_supersedes_tail_edit` | Valid rerun checkpoint is retained and replaced-tail edit provenance remains explicit |
| Rerun failure | `test_active_rerun_failure_discards_partial_tail_but_keeps_history` and `test_zero_frame_rerun_failure_restores_tail_without_superseding_edits` | Active failure keeps only the prefix; zero-frame failure restores the entire old tail |
| Close while tracking | `test_closing_window_cancels_tracking_worker_and_blocks_new_jobs` | Close gate waits for worker/process cleanup and rejects new work |
| Terminal/thread gap | `test_tracking_terminal_signal_stays_locked_until_thread_exits` and `test_closing_tracking_terminal_gap_replaces_stale_success_status` | Domain terminal is stable while controls remain locked until QThread exit |
| Stale progress | `test_zero_frame_cancel_restores_prior_state_and_rejects_late_progress` | Progress after a terminal state cannot commit replacement or metrics |

The existing `TrackingWorker` suite separately covers bounded prefetch, checkpoint transport, spawn isolation, cancel grace, terminate/kill escalation, source identity checks before/after processing, and unresponsive subprocess failure reporting.
