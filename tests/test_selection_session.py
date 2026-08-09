from __future__ import annotations

import unittest

import numpy as np

from neo_tracker.kinematics import SampleSeries
from neo_tracker.ui.selection_session import (
    SelectionMatch,
    SelectionOrigin,
    SelectionSession,
)


REVISION = "results:selection-fixture"


def series(
    *,
    series_id: str = "filtered:x",
    frames: tuple[int, ...] = (10, 14, 18),
    times: tuple[float, ...] = (1.0, 1.4, 1.8),
    valid: tuple[bool, ...] = (True, True, True),
    source_revision: str = REVISION,
) -> SampleSeries:
    return SampleSeries(
        series_id=series_id,
        name="Filtered x",
        frame_indices=np.asarray(frames, dtype=np.int64),
        time_s=np.asarray(times, dtype=np.float64),
        values=np.arange(len(frames), dtype=np.float64),
        valid_mask=np.asarray(valid, dtype=bool),
        unit="m",
        source_kind="filtered_state",
        source_revision=source_revision,
    )


class SelectionSessionTests(unittest.TestCase):
    def make_session(self) -> SelectionSession:
        session = SelectionSession()
        activated = session.activate_context(
            task_id="task-a",
            result_identity="result-a",
            source_revision=REVISION,
        )
        self.assertTrue(activated.accepted)
        self.assertTrue(session.attach_series(series()).accepted)
        return session

    def test_video_frame_maps_to_nearest_sample_and_tie_prefers_earlier(self) -> None:
        session = self.make_session()

        outcome = session.select_frame(
            12,
            origin=SelectionOrigin.VIDEO,
            expected_source_revision=REVISION,
        )

        self.assertTrue(outcome.changed)
        self.assertEqual(outcome.state.selected_frame_index, 12)
        self.assertEqual(outcome.state.selected_sample_index, 0)
        self.assertEqual(outcome.state.selected_time_s, 1.0)
        self.assertEqual(outcome.state.match, SelectionMatch.NEAREST)
        self.assertTrue(outcome.state.selected_sample_valid)

    def test_table_sample_maps_exactly_to_video_and_preserves_invalid_status(self) -> None:
        session = SelectionSession()
        session.activate_context("task-a", "result-a", REVISION)
        session.attach_series(series(valid=(True, False, True)))

        outcome = session.select_sample(
            "filtered:x",
            1,
            origin=SelectionOrigin.TABLE,
            expected_source_revision=REVISION,
        )

        self.assertEqual(outcome.state.selected_frame_index, 14)
        self.assertEqual(outcome.state.selected_time_s, 1.4)
        self.assertEqual(outcome.state.match, SelectionMatch.EXACT)
        self.assertFalse(outcome.state.selected_sample_valid)

    def test_plot_time_uses_true_series_time_and_nearest_valid_sample(self) -> None:
        session = SelectionSession()
        session.activate_context("task-a", "result-a", REVISION)
        session.attach_series(series(valid=(True, False, True)))

        outcome = session.select_time(
            1.39,
            origin=SelectionOrigin.PLOT,
            expected_source_revision=REVISION,
        )

        self.assertEqual(outcome.state.selected_sample_index, 0)
        self.assertEqual(outcome.state.selected_frame_index, 10)
        self.assertEqual(outcome.state.selected_time_s, 1.0)
        self.assertEqual(outcome.state.match, SelectionMatch.NEAREST)

    def test_stale_source_revision_is_rejected_without_broadcast(self) -> None:
        session = self.make_session()
        observed = []
        session.subscribe(observed.append)
        before = session.state

        outcome = session.select_frame(
            14,
            origin=SelectionOrigin.DIAGNOSTIC,
            expected_source_revision="results:stale",
        )

        self.assertFalse(outcome.accepted)
        self.assertFalse(outcome.changed)
        self.assertEqual(outcome.reason, "stale-source-revision")
        self.assertIs(session.state, before)
        self.assertEqual(observed, [])

    def test_task_switch_clears_old_selection_and_attached_series(self) -> None:
        session = self.make_session()
        session.select_sample("filtered:x", 2, origin=SelectionOrigin.TABLE)

        outcome = session.activate_context(
            task_id="task-b",
            result_identity="result-b",
            source_revision="results:b",
        )

        self.assertTrue(outcome.changed)
        self.assertEqual(outcome.state.selected_task_id, "task-b")
        self.assertEqual(outcome.state.source_revision, "results:b")
        self.assertIsNone(outcome.state.selected_frame_index)
        self.assertIsNone(outcome.state.selected_series_id)
        self.assertEqual(session.series_ids, ())

    def test_semantic_noop_and_reentrant_listener_do_not_loop(self) -> None:
        session = self.make_session()
        observed = []
        nested = []

        def listener(event) -> None:
            observed.append(event)
            nested.append(
                session.select_sample(
                    event.current.selected_series_id or "",
                    event.current.selected_sample_index or 0,
                    origin=SelectionOrigin.TABLE,
                    caused_by_revision=event.current.selection_revision,
                )
            )

        session.subscribe(listener)
        first = session.select_frame(14, origin=SelectionOrigin.VIDEO)
        second = session.select_frame(14, origin=SelectionOrigin.VIDEO)

        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertEqual(len(observed), 1)
        self.assertEqual(len(nested), 1)
        self.assertFalse(nested[0].changed)
        self.assertEqual(nested[0].reason, "selection-echo")

    def test_selection_revision_increases_once_per_semantic_commit(self) -> None:
        session = self.make_session()
        start_revision = session.state.selection_revision
        events = []
        session.subscribe(events.append)

        session.select_frame(10, origin=SelectionOrigin.VIDEO)
        session.select_frame(10, origin=SelectionOrigin.PLOT)
        session.select_fit("fit-1", origin=SelectionOrigin.FIT)

        self.assertEqual(session.state.selection_revision, start_revision + 2)
        self.assertEqual([event.current.selection_revision for event in events], [start_revision + 1, start_revision + 2])
        self.assertEqual(events[0].origin, SelectionOrigin.VIDEO)
        self.assertEqual(events[1].origin, SelectionOrigin.FIT)

    def test_invalid_inputs_fail_without_mutating_state(self) -> None:
        session = self.make_session()
        before = session.state
        with self.assertRaisesRegex(ValueError, "frame index"):
            session.select_frame(-1, origin=SelectionOrigin.VIDEO)
        with self.assertRaisesRegex(IndexError, "sample index"):
            session.select_sample("filtered:x", 99, origin=SelectionOrigin.TABLE)
        with self.assertRaisesRegex(ValueError, "finite"):
            session.select_time(float("nan"), origin=SelectionOrigin.PLOT)
        self.assertIs(session.state, before)


if __name__ == "__main__":
    unittest.main()
