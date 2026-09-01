from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from neo_tracker.kinematics.types import DerivativeConfig, FitRequest
from neo_tracker.media import MediaInfo
from neo_tracker.presets import default_preset_registry
from neo_tracker.project import (
    ANALYSIS_WORKSPACE_SCHEMA_REVISION,
    MAX_ANALYSIS_DEFINITIONS_PER_TASK,
    AnalysisDefinition,
    AnalysisSourceReference,
    AnalysisWorkspaceSnapshot,
    NeoTrackerProject,
    ProjectTaskSnapshot,
    project_content_fingerprint,
)
from neo_tracker.ui.project_controller import ProjectTaskController
from neo_tracker.ui.project_open_worker import load_project_isolated
from threading import Event


TASK_ID = "00000000-0000-4000-8000-000000000123"


def analysis_definition(
    *,
    task_id: str = TASK_ID,
    analysis_id: str = "velocity-fit",
    view_state: dict[str, object] | None = None,
) -> AnalysisDefinition:
    source = AnalysisSourceReference(
        task_id=task_id,
        series_id="state:x",
        source_kind="state",
        source_revision="sha256:current-results",
    )
    return AnalysisDefinition(
        analysis_id=analysis_id,
        name="Horizontal velocity fit",
        source_series=source,
        derivative_config=DerivativeConfig(
            method="nonuniform_finite_difference",
            order=1,
            edge_policy="one_sided",
        ).to_dict(),
        smoothing_config={
            "method": "savgol_uniform",
            "window_length": 9,
            "polyorder": 3,
            "uniformity_tolerance": 0.001,
            "edge_policy": "invalid",
            "gap_policy": "split",
        },
        fit_config=FitRequest(
            series_id="state:x:d1",
            model="linear",
            range_start_s=0.1,
            range_end_s=2.5,
            source_revision=source.source_revision,
        ).to_dict(),
        visible=True,
        selected_range_s=(0.1, 2.5),
        view_state=view_state or {"page": "Plot", "residual": False},
        provenance={"created_by": "NeoTracker", "contract": "v0.3"},
    )


class KinematicsProjectSchemaTests(unittest.TestCase):
    def project(self, definition: AnalysisDefinition | None = None) -> NeoTrackerProject:
        workspace = AnalysisWorkspaceSnapshot(
            definitions=(() if definition is None else (definition,))
        )
        return NeoTrackerProject(
            name="kinematics-persistence",
            tasks=[
                ProjectTaskSnapshot(
                    media_path=None,
                    pipeline_key="color_marker",
                    task_id=TASK_ID,
                    analysis_workspace=workspace,
                )
            ],
        )

    def test_v3_roundtrip_persists_only_bounded_analysis_definitions(self) -> None:
        project = self.project(analysis_definition())

        encoded = project.to_dict()
        restored = NeoTrackerProject.from_dict(json.loads(json.dumps(encoded)))
        text = json.dumps(encoded, sort_keys=True)

        self.assertEqual(encoded["version"], 3)
        self.assertEqual(restored.to_dict(), encoded)
        self.assertEqual(
            encoded["tasks"][0]["analysis_workspace"]["schema_revision"],
            ANALYSIS_WORKSPACE_SCHEMA_REVISION,
        )
        for forbidden in (
            "frame_indices",
            "time_s\"",
            "values",
            "valid_mask",
            "data_b64",
            "predicted",
            "residuals",
            "decimation",
            "cache",
        ):
            self.assertNotIn(forbidden, text)

    def test_analysis_definition_changes_participate_in_fingerprint(self) -> None:
        first = self.project(analysis_definition(view_state={"page": "Plot"}))
        second = self.project(analysis_definition(view_state={"page": "Data"}))

        self.assertNotEqual(
            project_content_fingerprint(first),
            project_content_fingerprint(second),
        )

    def test_v1_and_v2_tasks_migrate_to_v3_with_stable_identity_after_save(self) -> None:
        for version in (1, 2):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                task = {"media_path": None, "pipeline_key": "color_marker", "results": []}
                payload: dict[str, object] = {
                    "format": "neo-tracker-project",
                    "version": version,
                    "name": "legacy",
                    "media_paths": [],
                    "tasks": [task],
                    "notes": "preserved",
                }
                payload["pipelines" if version == 1 else "pipeline_library"] = []
                migrated = NeoTrackerProject.from_dict(payload)
                first_id = migrated.tasks[0].task_id
                path = Path(directory) / "migrated.ntproj"
                migrated.save(path)
                reopened = NeoTrackerProject.load(path)

                self.assertEqual(reopened.to_dict()["version"], 3)
                self.assertEqual(reopened.tasks[0].task_id, first_id)
                self.assertEqual(reopened.tasks[0].analysis_workspace.definitions, ())
                self.assertEqual(reopened.notes, "preserved")

    def test_v3_unknown_fields_types_and_identity_mismatches_fail_closed(self) -> None:
        valid = self.project(analysis_definition()).to_dict()
        mutations = []

        top_unknown = json.loads(json.dumps(valid))
        top_unknown["unexpected"] = True
        mutations.append((top_unknown, "unknown fields"))

        task_unknown = json.loads(json.dumps(valid))
        task_unknown["tasks"][0]["unexpected"] = True
        mutations.append((task_unknown, "unknown fields"))

        unknown_type = json.loads(json.dumps(valid))
        unknown_type["tasks"][0]["analysis_workspace"]["definitions"][0][
            "analysis_type"
        ] = "plugin"
        mutations.append((unknown_type, "unknown analysis_type"))

        unknown_definition_field = json.loads(json.dumps(valid))
        unknown_definition_field["tasks"][0]["analysis_workspace"]["definitions"][0][
            "arrays"
        ] = {"data_b64": "AAAA"}
        mutations.append((unknown_definition_field, "unknown fields"))

        wrong_task = json.loads(json.dumps(valid))
        wrong_task["tasks"][0]["analysis_workspace"]["definitions"][0]["source_series"][
            "task_id"
        ] = "00000000-0000-4000-8000-000000000999"
        mutations.append((wrong_task, "must match"))

        missing_task_id = json.loads(json.dumps(valid))
        del missing_task_id["tasks"][0]["task_id"]
        mutations.append((missing_task_id, "task_id"))

        bad_revision = json.loads(json.dumps(valid))
        bad_revision["tasks"][0]["analysis_workspace"]["schema_revision"] = 2
        mutations.append((bad_revision, "schema_revision"))

        implicit_resampling = json.loads(json.dumps(valid))
        implicit_resampling["tasks"][0]["analysis_workspace"]["definitions"][0][
            "smoothing_config"
        ]["resample"] = True
        mutations.append((implicit_resampling, "resample must be false"))

        empty_analysis = json.loads(json.dumps(valid))
        empty_definition = empty_analysis["tasks"][0]["analysis_workspace"]["definitions"][0]
        empty_definition["derivative_config"] = None
        empty_definition["smoothing_config"] = None
        empty_definition["fit_config"] = None
        mutations.append((empty_analysis, "requires derivative, smoothing, or fit"))

        misbound_fit = json.loads(json.dumps(valid))
        fit_definition = misbound_fit["tasks"][0]["analysis_workspace"]["definitions"][0]
        fit_definition["derivative_config"] = None
        fit_definition["smoothing_config"] = None
        fit_definition["fit_config"]["series_id"] = "state:y"
        mutations.append((misbound_fit, "fit_config series_id must match source_series"))

        for payload, message in mutations:
            with self.subTest(message=message), self.assertRaisesRegex(
                (TypeError, ValueError), message
            ):
                NeoTrackerProject.from_dict(payload)

    def test_definition_limits_and_numpy_values_are_rejected_before_serialization(self) -> None:
        with self.assertRaisesRegex(TypeError, "NumPy"):
            analysis_definition(view_state={"array": np.arange(3)})

        definitions = tuple(
            analysis_definition(analysis_id=f"analysis-{index}")
            for index in range(MAX_ANALYSIS_DEFINITIONS_PER_TASK + 1)
        )
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            AnalysisWorkspaceSnapshot(definitions=definitions)

    def test_definition_metadata_is_detached_and_deeply_read_only(self) -> None:
        source = {"nested": {"page": "Plot"}}
        definition = analysis_definition(view_state=source)
        source["nested"]["page"] = "Data"

        self.assertEqual(definition.to_dict()["view_state"]["nested"]["page"], "Plot")
        with self.assertRaises(TypeError):
            definition.view_state["nested"] = {}  # type: ignore[index]


class KinematicsProjectControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        registry = default_preset_registry()
        self.controller = ProjectTaskController(
            registry,
            "color_marker",
            media_probe=lambda _path: MediaInfo(available=False),
        )

    def test_task_identity_and_definitions_survive_controller_roundtrip(self) -> None:
        task = self.controller.new_task(None, "color_marker")
        definition = analysis_definition(task_id=task.task_id)
        task.analysis_workspace = AnalysisWorkspaceSnapshot((definition,))

        restored = self.controller.task_from_snapshot(
            self.controller.snapshot_from_task(task)
        )

        self.assertEqual(restored.task_id, task.task_id)
        self.assertEqual(restored.analysis_workspace, task.analysis_workspace)
        self.assertEqual(restored.results_generation, 0)

    def test_relink_clear_preserves_definitions_and_invalidates_runtime_generation(self) -> None:
        task = self.controller.new_task("old.mp4", "color_marker")
        task.analysis_workspace = AnalysisWorkspaceSnapshot(
            (analysis_definition(task_id=task.task_id),)
        )
        before = task.results_generation

        self.controller.relink_media(
            task,
            "new.mp4",
            MediaInfo(
                fps=30.0,
                frame_count=10,
                width=640,
                height=360,
                duration_s=1.0,
                available=True,
            ),
            clear_results=True,
        )

        self.assertEqual(task.results_generation, before + 1)
        self.assertEqual(len(task.analysis_workspace.definitions), 1)


class KinematicsIsolatedOpenTests(unittest.TestCase):
    def test_isolated_open_preserves_analysis_definitions_without_materializing_arrays(self) -> None:
        project = KinematicsProjectSchemaTests().project(analysis_definition())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis.ntproj"
            project.save(path)
            restored = load_project_isolated(path, Event())

        self.assertEqual(restored.tasks[0].task_id, TASK_ID)
        self.assertEqual(
            restored.tasks[0].analysis_workspace,
            project.tasks[0].analysis_workspace,
        )


if __name__ == "__main__":
    unittest.main()
