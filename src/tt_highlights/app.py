"""Streamlit UI for TT Highlight Clipper — Manual Clip Editor."""

import json
import logging
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

import streamlit as st
import streamlit.components.v1 as components

from tt_highlights.job import create_job, load_job, artifacts_dir, exports_dir, debug_dir
from tt_highlights.config import load_config
from tt_highlights.steps import get_step_function
from tt_highlights.media_server import start_media_server, get_media_url
from tt_highlights.recent import add_recent_job, get_recent_jobs, remove_recent_job
from tt_highlights.diagnose import diagnose_missed_rally, explain_detected_rally
from tt_highlights.steps.setup import is_setup_complete

logger = logging.getLogger(__name__)

_FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
_video_editor_component = components.declare_component(
    "video_editor", path=str(_FRONTEND_DIR),
)
_roi_picker_component = components.declare_component(
    "roi_picker", path=str(_FRONTEND_DIR / "roi_picker"),
)

st.set_page_config(page_title="TT Highlight Clipper", layout="wide")


_DEFAULT_BROWSE_DIR = r"D:\CicadaKim\Project\tt-highlight-clipper\video"


def _browse_file(
    title: str = "Select File",
    filetypes: list[tuple[str, str]] | None = None,
    initialdir: str | None = None,
) -> str | None:
    """Open native file dialog and return selected path."""
    if initialdir is None:
        initialdir = _DEFAULT_BROWSE_DIR
    if not Path(initialdir).is_dir():
        initialdir = None
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(
        title=title,
        initialdir=initialdir,
        filetypes=filetypes or [("All files", "*.*")],
    )
    root.destroy()
    return path if path else None


def main():
    st.title("TT Highlight Clipper")
    st.caption("Manual clip editor for table tennis highlights")

    # Initialize session state
    if "job_path" not in st.session_state:
        st.session_state.job_path = None
    if "current_screen" not in st.session_state:
        st.session_state.current_screen = "setup"
    if "clips" not in st.session_state:
        st.session_state.clips = []

    _sidebar()

    screen = st.session_state.current_screen
    if screen == "setup":
        _screen_setup()
    elif screen == "editor":
        _screen_clip_editor()
    elif screen == "export":
        _screen_export()


# ─── Sidebar ─────────────────────────────────────────────────────────────────

def _sidebar():
    with st.sidebar:
        st.header("Navigation")

        screens = [
            ("setup", "1. Setup"),
            ("editor", "2. Clip Editor"),
            ("export", "3. Export"),
        ]

        for key, label in screens:
            if st.button(label, key=f"nav_{key}", use_container_width=True):
                st.session_state.current_screen = key
                st.rerun()

        st.divider()

        if st.session_state.job_path:
            st.success("Job loaded")
            jp = Path(st.session_state.job_path)
            st.caption(f"Job: {jp.parent.name}")

            if st.button("Reset Job"):
                st.session_state.job_path = None
                st.session_state.clips = []
                st.session_state.current_screen = "setup"
                st.rerun()


# ─── Screen 1: Setup ─────────────────────────────────────────────────────────

def _screen_setup():
    st.header("1. Setup")

    tab_new, tab_load, tab_recent = st.tabs(["New Job", "Load Existing", "Recent Jobs"])

    with tab_new:
        # Apply browse result before widget renders
        if "_picked_video" in st.session_state:
            st.session_state.video_path_input = st.session_state.pop("_picked_video")

        col1, col2 = st.columns([5, 1])
        with col1:
            video_path = st.text_input(
                "Video file path",
                key="video_path_input",
            )
        with col2:
            st.write("")  # spacing
            if st.button("Browse", key="browse_video"):
                picked = _browse_file(
                    title="Select Video File",
                    filetypes=[("Video files", "*.mp4 *.avi *.mkv *.mov"), ("All files", "*.*")],
                )
                if picked:
                    st.session_state._picked_video = picked
                    st.rerun()

        # Auto-compute output directory
        if video_path and Path(video_path).suffix:
            video_p = Path(video_path)
            out_dir = str(video_p.parent / f"{video_p.stem}_out")
            st.caption(f"Output: {out_dir}")
        else:
            out_dir = "out"

        if st.button("Create Job & Preprocess", type="primary"):
            if not video_path:
                st.error("Please enter a video path or use Browse.")
                return

            try:
                job_path = create_job(video_path, out_dir)
                st.session_state.job_path = str(job_path)
                _run_step("preprocess")
                st.session_state.clips = _load_clips(str(job_path))
                st.success("Job created and preprocessed! Set up ROI below.")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")


    with tab_load:
        # Apply browse result before widget renders
        if "_picked_job" in st.session_state:
            st.session_state.existing_job_input = st.session_state.pop("_picked_job")

        col_l1, col_l2 = st.columns([5, 1])
        with col_l1:
            existing_job = st.text_input(
                "Path to existing job.json", key="existing_job_input",
            )
        with col_l2:
            st.write("")  # spacing
            if st.button("Browse", key="browse_job"):
                picked = _browse_file(
                    title="Select job.json",
                    filetypes=[("Job files", "*.json"), ("All files", "*.*")],
                )
                if picked:
                    st.session_state._picked_job = picked
                    st.rerun()
        if st.button("Load Job"):
            if existing_job and Path(existing_job).exists():
                st.session_state.job_path = existing_job
                st.session_state.clips = _load_clips(existing_job)
                job_data = load_job(existing_job)
                add_recent_job(existing_job, job_data)
                st.success("Job loaded.")
                # Auto-navigate to editor if preprocess is done
                art = artifacts_dir(existing_job)
                if (art / "video_meta.json").exists():
                    st.session_state.current_screen = "editor"
                else:
                    st.session_state.current_screen = "setup"
                st.rerun()
            else:
                st.error("job.json not found.")

    with tab_recent:
        recent = get_recent_jobs()
        if not recent:
            st.info("No recent jobs.")
        else:
            for idx, rj in enumerate(recent):
                col1, col2, col3 = st.columns([4, 1, 1])
                with col1:
                    st.markdown(f"**{rj['video_name']}**")
                    opened = rj.get("last_opened", "")[:10]
                    st.caption(f"Last opened: {opened} | {rj['job_path']}")
                with col2:
                    if st.button("Open", key=f"recent_open_{idx}"):
                        jp = rj["job_path"]
                        st.session_state.job_path = jp
                        st.session_state.clips = _load_clips(jp)
                        job_data = load_job(jp)
                        add_recent_job(jp, job_data)
                        art = artifacts_dir(jp)
                        if (art / "video_meta.json").exists():
                            st.session_state.current_screen = "editor"
                        else:
                            st.session_state.current_screen = "setup"
                        st.rerun()
                with col3:
                    if st.button("Remove", key=f"recent_rm_{idx}"):
                        remove_recent_job(rj["job_path"])
                        st.rerun()

    # Preview if job is loaded
    if not st.session_state.job_path:
        return

    st.divider()
    art = artifacts_dir(st.session_state.job_path)

    frame0_path = art / "frame0.jpg"
    if frame0_path.exists():
        st.image(str(frame0_path), caption="Frame 0")

    meta_path = art / "video_meta.json"
    if meta_path.exists():
        with open(meta_path, "r") as f:
            meta = json.load(f)
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Duration", f"{meta['duration_sec']:.1f}s")
        col2.metric("FPS", meta["fps"])
        col3.metric("Resolution", f"{meta['width']}x{meta['height']}")
        col4.metric("Has Audio", str(meta["has_audio"]))

    proxy_path = art / "proxy.mp4"
    if proxy_path.exists():
        st.video(str(proxy_path))

    # ── ROI Setup section ─────────────────────────────────────────────────
    st.divider()
    st.subheader("ROI Setup")

    setup_done = is_setup_complete(st.session_state.job_path)
    setup_state = _load_setup_state(art)

    if setup_done:
        st.success("Setup complete — ROI detected.")
    elif setup_state and setup_state.get("requires_review"):
        st.warning(
            "Low confidence auto-detection — manual review recommended. "
            "Confirm the ROI below or edit it manually."
        )
        for w in setup_state.get("warnings", []):
            st.caption(w)
        if st.button("Confirm ROI (accept as-is)", key="confirm_setup_roi", type="primary"):
            _mark_setup_complete(art)
            st.success("Setup confirmed!")
            st.rerun()

    # Show current ROI info
    roi_path = art / "table_roi.json"
    if roi_path.exists():
        with open(roi_path, "r") as f:
            roi_info = json.load(f)
        st.caption(
            f"Table ROI: {roi_info.get('source', 'unknown')} "
            f"(confidence: {roi_info.get('confidence', 'N/A')})"
        )
    overlay_path = Path(st.session_state.job_path).parent / "debug" / "frame0_overlay.png"
    if overlay_path.exists():
        st.image(str(overlay_path), caption="Table ROI overlay")

    # Auto-detect button
    if st.button("Run Auto-detect ROI", key="run_setup"):
        try:
            _run_step("setup")
            # Check if review is needed after auto-detect
            new_state = _load_setup_state(art)
            if new_state and new_state.get("requires_review") and not new_state.get("completed"):
                st.warning("Low confidence detection. Please review and confirm the ROI.")
            else:
                st.success("Setup auto-detection complete!")
            st.rerun()
        except Exception as e:
            st.error(f"Setup failed: {e}")

    # Canvas-based ROI editing
    if frame0_path.exists():
        _roi_canvas_editor(art, frame0_path)

    if st.button("Next: Clip Editor", type="primary"):
        if not is_setup_complete(st.session_state.job_path):
            st.warning("Please complete ROI setup first (click 'Run Auto-detect ROI' or save manual ROI).")
        else:
            st.session_state.current_screen = "editor"
            st.rerun()


# ─── Screen 2: Clip Editor ───────────────────────────────────────────────────

def _screen_clip_editor():
    st.header("2. Clip Editor")

    if not _check_job():
        return

    art = artifacts_dir(st.session_state.job_path)

    meta_path = art / "video_meta.json"
    if not meta_path.exists():
        st.warning("Preprocess not done. Go to Setup first.")
        return

    with open(meta_path, "r") as f:
        meta = json.load(f)
    duration = meta["duration_sec"]
    fps = meta.get("fps", 30)

    # ── Start media server for proxy video ────────────────────────────────
    proxy_path = art / "proxy.mp4"
    video_url = ""
    if proxy_path.exists():
        port = start_media_server(str(art))
        video_url = get_media_url(port, "proxy.mp4")
    else:
        st.warning(f"proxy.mp4 not found: {proxy_path}")

    # ── Collect markers from audio events ─────────────────────────────────
    markers = []
    events_path = art / "audio_events.json"
    if events_path.exists():
        try:
            with open(events_path, "r") as f:
                events_data = json.load(f)
            markers = [e["time"] for e in events_data.get("impacts", [])]
        except Exception:
            pass

    # ── Auto-detect section ──────────────────────────────────────────────
    cfg = _load_job_config()

    with st.expander("Audio Detection Parameters", expanded=False):
        st.caption("PANNs 모델로 탁구공 타격음·환호성을 감지하는 파라미터")
        ac = cfg["audio"]
        col_a1, col_a2 = st.columns(2)
        with col_a1:
            a_impact_thresh = st.slider(
                "Impact threshold",
                0.01, 0.20, ac["impact_threshold"], 0.01,
                help="타격음 감지 확률 임계값.\n"
                     "⬆ 올리면: 확실한 타격만 감지, 약한 타격 놓침\n"
                     "⬇ 낮추면: 더 많이 감지되지만 오탐(발소리, 잡음) 증가\n"
                     "💡 시끄러운 환경: 0.08~0.12 / 조용한 체육관: 0.03~0.05",
                key="p_impact_threshold",
            )
            a_impact_min_dist = st.slider(
                "Impact min distance (sec)",
                0.05, 0.50, ac["impact_min_distance_sec"], 0.01,
                help="연속 타격 간 최소 간격.\n"
                     "⬆ 올리면: 한 타격이 중복 감지되지 않음, 빠른 랠리 놓침\n"
                     "⬇ 낮추면: 빠른 드라이브 감지, 한 타격이 여러 번 잡힐 수 있음\n"
                     "💡 빠른 드라이브: 0.08~0.12 / 느린 커트: 0.15~0.25",
                key="p_impact_min_dist",
            )
        with col_a2:
            a_cheer_thresh = st.slider(
                "Cheer threshold",
                0.01, 0.20, ac["cheer_threshold"], 0.01,
                help="환호/박수 감지 확률 임계값.\n"
                     "⬆ 올리면: 확실한 환호만 감지\n"
                     "⬇ 낮추면: 작은 박수도 감지, 잡음 오탐 증가\n"
                     "💡 환호 없는 영상: 0.08 이상으로 올려서 오탐 방지",
                key="p_cheer_threshold",
            )
            a_cheer_min_len = st.slider(
                "Cheer min length (sec)",
                0.1, 2.0, ac["cheer_min_len_sec"], 0.1,
                help="이 시간보다 짧은 환호 구간은 무시.\n"
                     "⬆ 올리면: 긴 환호만 인정, 짧은 박수 제거\n"
                     "⬇ 낮추면: 짧은 반응도 포함\n"
                     "💡 동호회: 0.3~0.5 / 대회: 0.8~1.5",
                key="p_cheer_min_len",
            )

    with st.expander("Rally Segmentation Parameters", expanded=False):
        st.caption("타격 이벤트를 그룹핑하여 랠리 구간을 결정하는 파라미터")
        sg = cfg["segmentation"]
        col_s1, col_s2 = st.columns(2)
        with col_s1:
            s_gap_max = st.slider(
                "Impact gap max (sec)",
                1.0, 8.0, sg["impact_gap_max_sec"], 0.5,
                help="타격 간 이 시간 이상 비면 랠리 종료로 판단.\n"
                     "⬆ 올리면: 끊어진 타격도 하나의 랠리로 합침\n"
                     "⬇ 낮추면: 조금만 비어도 별도 랠리로 분리\n"
                     "💡 쪼개지면: 4~5초 / 합쳐지면: 2~3초",
                key="p_gap_max",
            )
            s_min_impacts = st.number_input(
                "Min impacts per rally",
                1, 20, sg["min_impacts"],
                help="최소 타격 수. 이보다 적은 랠리는 제거.\n"
                     "⬆ 올리면: 의미 있는 긴 랠리만 남김\n"
                     "⬇ 낮추면: 서브 에이스 등 짧은 포인트도 포함\n"
                     "💡 서브 에이스 포함: 1~2 / 의미 있는 랠리만: 3~4",
                key="p_min_impacts",
            )
            s_min_duration = st.slider(
                "Min rally duration (sec)",
                0.5, 10.0, sg["min_rally_duration_sec"], 0.5,
                help="최소 랠리 길이. 이보다 짧으면 제거.\n"
                     "⬆ 올리면: 짧은 포인트 제거, 긴 랠리만 남김\n"
                     "⬇ 낮추면: 짧은 서브도 포함\n"
                     "💡 하이라이트용: 3~5초 / 전체 분석: 1~2초",
                key="p_min_duration",
            )
            s_end_grace = st.slider(
                "End grace (sec)",
                0.0, 5.0, sg["end_grace_sec"], 0.1,
                help="마지막 타격 이후 추가로 포함할 여유 시간.\n"
                     "⬆ 올리면: 세레모니/리액션까지 포함\n"
                     "⬇ 낮추면: 랠리만 깔끔하게 자름\n"
                     "💡 세레모니 포함: 2~3초 / 랠리만 깔끔하게: 1~1.5초",
                key="p_end_grace",
            )
        with col_s2:
            s_activity_min = st.slider(
                "Activity min mean",
                0.0, 0.20, sg["activity_min_mean"], 0.01,
                help="평균 움직임이 이보다 낮은 구간은 랠리가 아닌 것으로 판단.\n"
                     "⬆ 올리면: 움직임이 확실한 랠리만 남김, 느린 경기 놓침\n"
                     "⬇ 낮추면: 느린 경기도 포함, 잡음 구간 포함 가능\n"
                     "💡 옆 테이블 소음 많으면: 0.08~0.15로 올리기",
                key="p_activity_min",
            )
            s_merge_gap = st.slider(
                "Merge gap (sec)",
                0.5, 5.0, sg["merge_gap_sec"], 0.5,
                help="인접 랠리 간 간격이 이보다 짧으면 하나로 합침.\n"
                     "⬆ 올리면: 여러 포인트를 하나의 클립으로 묶음\n"
                     "⬇ 낮추면: 포인트별로 분리\n"
                     "💡 포인트별 분리: 1~2초 / 여러 포인트 묶기: 3~5초",
                key="p_merge_gap",
            )
            s_split_min_dur = st.slider(
                "Split min duration (sec)",
                3.0, 15.0, sg["split_min_duration_sec"], 1.0,
                help="이 길이 이상의 랠리에 대해 움직임 저점에서 분할 시도.\n"
                     "⬆ 올리면: 긴 랠리도 유지, 분할 줄어듦\n"
                     "⬇ 낮추면: 적극적으로 분할\n"
                     "💡 합쳐지면: 6~8초 / 분할 잦으면: 12~15초",
                key="p_split_min_dur",
            )
            s_boundary_grad = st.slider(
                "Boundary gradient threshold",
                0.005, 0.10, sg["boundary_grad_threshold"], 0.005,
                help="시작/끝 경계 보정 시 움직임 변화량 기준.\n"
                     "⬆ 올리면: 큰 움직임 변화에서만 경계 보정\n"
                     "⬇ 낮추면: 세밀하게 경계 보정\n"
                     "💡 기본값 0.02 적정. 들쭉날쭉하면: 0.04~0.06",
                key="p_boundary_grad",
            )
        s_require_video = st.checkbox(
            "영상 확인 필터 (다중 테이블 환경용)",
            value=sg.get("require_video_confirmation", False),
            help="각 타격음에 대해 영상 움직임을 확인합니다.\n"
                 "옆 테이블 소리로 인한 오탐을 제거합니다.\n"
                 "💡 여러 대가 동시에 치는 환경에서 사용하세요.",
            key="p_require_video",
        )

    # Build config override from UI values
    config_override = {
        "audio": {
            "impact_threshold": a_impact_thresh,
            "impact_min_distance_sec": a_impact_min_dist,
            "cheer_threshold": a_cheer_thresh,
            "cheer_min_len_sec": a_cheer_min_len,
        },
        "segmentation": {
            "impact_gap_max_sec": s_gap_max,
            "min_impacts": s_min_impacts,
            "min_rally_duration_sec": s_min_duration,
            "end_grace_sec": s_end_grace,
            "activity_min_mean": s_activity_min,
            "merge_gap_sec": s_merge_gap,
            "split_min_duration_sec": s_split_min_dur,
            "boundary_grad_threshold": s_boundary_grad,
            "require_video_confirmation": s_require_video,
        },
    }

    # ── Saved parameter suggestions ──────────────────────────────────────
    saved_sugg = _load_parameter_suggestions()
    if saved_sugg and saved_sugg.get("suggestions"):
        sugg = saved_sugg["suggestions"]
        col_sl, col_sr = st.columns(2)
        with col_sl:
            if st.button("Load Previous Suggestions", key="load_suggestions"):
                for param_key, value in sugg.items():
                    widget_key = _PARAM_KEY_MAP.get(param_key)
                    if widget_key:
                        st.session_state[widget_key] = value
                st.success("Suggestions loaded into sliders.")
                st.rerun()
        with col_sr:
            if st.button("Revert Suggestions", key="revert_suggestions"):
                art_dir = artifacts_dir(st.session_state.job_path)
                sugg_path = art_dir / "parameter_suggestions.json"
                if sugg_path.exists():
                    sugg_path.unlink()
                st.success("Suggestions reverted.")
                st.rerun()
        st.caption(
            f"Saved suggestions: {sugg} "
            f"(from {saved_sugg.get('created_at', '?')[:10]})"
        )

    # Gate: setup must be complete before auto-detect
    if not is_setup_complete(st.session_state.job_path):
        st.warning("ROI setup not completed. Go to Setup screen and run ROI detection first.")

    if st.button("Detect & Analyze", type="secondary",
                 disabled=not is_setup_complete(st.session_state.job_path)):
        try:
            pipeline = [
                "audio_events", "video_activity",
                "ball_tracking", "player_motion",
                "rally_segment",
                "pose_estimation", "scoreboard_ocr",
                "features", "scoring",
            ]
            for step_name in pipeline:
                override = _filter_override_for_step(config_override, step_name) or None
                _run_step(step_name, override)

            # Add detected rallies to clips
            rallies_path = art / "rallies.json"
            if rallies_path.exists():
                with open(rallies_path, "r") as f:
                    rallies_data = json.load(f)
                rallies = rallies_data.get("rallies", [])
                if rallies:
                    hl_cfg = _load_job_config().get("highlights", {})
                    hl_threshold = hl_cfg.get("auto_threshold", 0.4)
                    next_id = _next_clip_id()
                    for r in rallies:
                        end = r.get("end_refined", r["end"])
                        combined = r.get("segment_score", 0)
                        st.session_state.clips.append({
                            "id": next_id,
                            "rally_id": r["id"],
                            "clip_start": r["start"],
                            "clip_end": end,
                            "label": f"Rally {r['id']}",
                            "is_highlight": combined >= hl_threshold,
                            "conf_audio": r.get("conf_audio", 0),
                            "conf_video": r.get("conf_video", 0),
                            "conf_video_norm": r.get("conf_video_norm", 0),
                            "impact_count": r.get("impact_count", 0),
                            "rhythm_score": r.get("rhythm_score", 0),
                            "segment_score": combined,
                            "segment_flags": r.get("segment_flags", []),
                            "reason_end": r.get("reason_end_refined", ""),
                        })
                        next_id += 1
                    _save_clips(
                        st.session_state.job_path, st.session_state.clips,
                    )
                    st.success(f"Detect & Analyze complete! {len(rallies)} rallies found.")
                else:
                    st.info("Detect & Analyze complete but no rallies detected.")
            st.rerun()
        except Exception as e:
            st.error(f"Detect & Analyze failed: {e}")

    # ── Load activity curve & cheer segments ──────────────────────────────
    activity_samples = []
    activity_path = art / "activity.json"
    if activity_path.exists():
        with open(activity_path, "r") as f:
            act_data = json.load(f)
        activity_samples = act_data.get("samples", [])
        if len(activity_samples) > 2000:
            step = len(activity_samples) // 2000
            activity_samples = activity_samples[::step]

    cheer_segments = []
    try:
        cheer_segments = events_data.get("cheer_segments", [])
    except NameError:
        pass

    # ── Confidence filter ─────────────────────────────────────────────────
    display_clips = st.session_state.clips
    rallies_path = art / "rallies.json"
    if rallies_path.exists() and st.session_state.clips:
        min_conf = st.slider(
            "최소 신뢰도 필터", 0.0, 1.0, 0.0, 0.05,
            help="음성+영상 신뢰도 평균이 이 값 미만인 랠리를 숨깁니다.\n"
                 "재실행 없이 즉시 필터링.\n"
                 "💡 오탐이 많을 때 0.3~0.5로",
            key="p_min_confidence",
        )
        if min_conf > 0:
            display_clips = [
                c for c in st.session_state.clips
                if (c.get("conf_audio", 1) + c.get("conf_video", 1)) / 2 >= min_conf
                or c.get("conf_audio") is None
            ]

    # ── Rally Selector ────────────────────────────────────────────────────
    selected_rally_id = None
    rally_clips = [c for c in st.session_state.clips if c.get("rally_id") is not None]
    rally_map = {c["rally_id"]: c for c in rally_clips}
    rally_ids = sorted(rally_map.keys())
    if rally_ids:
        options = ["\u2014 Overview \u2014"] + [
            f"Rally {rid} [{rally_map[rid]['clip_start']:.1f}s \u2013 {rally_map[rid]['clip_end']:.1f}s]"
            for rid in rally_ids
        ]
        sel = st.selectbox("Inspect Rally", options, key="_rally_selector")
        if sel != "\u2014 Overview \u2014":
            selected_rally_id = rally_ids[options.index(sel) - 1]

    # ── Debug Panel / Inspector / Calibration ─────────────────────────────
    if selected_rally_id is not None:
        try:
            _render_inspector_panels(selected_rally_id, art)
        except Exception as e:
            st.error(f"Inspector error: {e}")

    # ── Interactive Video Editor component ────────────────────────────────
    component_value = _video_editor_component(
        video_url=video_url,
        duration=duration,
        fps=fps,
        clips=display_clips,
        markers=markers,
        activity=activity_samples,
        cheers=cheer_segments,
        key="video_editor",
        height=600,
    )

    # Handle actions from the component (with timestamp dedup to prevent reruns)
    if component_value and isinstance(component_value, dict):
        ts = component_value.get("_ts")
        if ts and ts != st.session_state.get("_last_editor_ts"):
            st.session_state._last_editor_ts = ts
            action = component_value.get("action")

            if action == "add_clip":
                clip_data = component_value.get("clip", {})
                next_id = _next_clip_id()
                st.session_state.clips.append({
                    "id": next_id,
                    "clip_start": clip_data.get("start", 0.0),
                    "clip_end": clip_data.get("end", 0.0),
                    "label": clip_data.get("label") or f"Clip {next_id}",
                    "is_highlight": False,
                })
                _save_clips(st.session_state.job_path, st.session_state.clips)
                st.rerun()

            elif action == "delete_clip":
                clip_id = component_value.get("clip_id")
                rally_id = None
                for c in st.session_state.clips:
                    if c["id"] == clip_id:
                        rally_id = c.get("rally_id")
                        break
                st.session_state.clips = [
                    c for c in st.session_state.clips if c["id"] != clip_id
                ]
                _save_clips(st.session_state.job_path, st.session_state.clips)
                _record_feedback_label(clip_id, rally_id, "exclude")
                st.rerun()

            elif action == "resize_clip":
                clip_id = component_value.get("clip_id")
                new_start = component_value.get("start")
                new_end = component_value.get("end")
                for c in st.session_state.clips:
                    if c["id"] == clip_id:
                        c["clip_start"] = new_start
                        c["clip_end"] = new_end
                        break
                _save_clips(st.session_state.job_path, st.session_state.clips)
                st.rerun()

            elif action == "toggle_highlight":
                clip_id = component_value.get("clip_id")
                is_highlight = component_value.get("is_highlight", False)
                rally_id = None
                for c in st.session_state.clips:
                    if c["id"] == clip_id:
                        c["is_highlight"] = is_highlight
                        rally_id = c.get("rally_id")
                        break
                _save_clips(st.session_state.job_path, st.session_state.clips)
                _record_feedback_label(
                    clip_id, rally_id,
                    "highlight" if is_highlight else "unhighlight",
                )
                st.rerun()

            elif action == "clear_all_clips":
                st.session_state.clips = []
                _save_clips(st.session_state.job_path, st.session_state.clips)
                st.rerun()

            elif action in ("export_clip_video", "export_clip_gif"):
                clip_start = component_value.get("clip_start")
                clip_end = component_value.get("clip_end")
                clip_label = component_value.get("label", "clip")
                fmt = "video" if action == "export_clip_video" else "gif"
                try:
                    path = _export_single_clip(
                        clip_start, clip_end, clip_label, fmt,
                    )
                    st.session_state._single_export_result = f"Exported: {path.name}"
                except Exception as e:
                    st.session_state._single_export_result = f"Export failed: {e}"
                st.rerun()

            elif action == "diagnose_clip":
                clip_id = component_value.get("clip_id")
                clip_start = component_value.get("clip_start")
                clip_end = component_value.get("clip_end")
                is_auto = component_value.get("is_auto", False)

                audio_ev_path = art / "audio_events.json"
                act_path = art / "activity.json"
                if not audio_ev_path.exists() or not act_path.exists():
                    st.session_state._diagnosis = {
                        "clip_id": clip_id, "error": "Run auto-detect first",
                    }
                else:
                    try:
                        diag_cfg = _load_job_config()
                        # Apply current slider overrides
                        from tt_highlights.config import _deep_merge
                        diag_cfg = _deep_merge(diag_cfg, config_override)
                        if is_auto:
                            clip_data = next(
                                (c for c in st.session_state.clips if c["id"] == clip_id),
                                None,
                            )
                            if clip_data:
                                result = explain_detected_rally(
                                    clip_data, audio_ev_path, act_path, diag_cfg,
                                )
                                st.session_state._diagnosis = {
                                    "clip_id": clip_id, "is_auto": True,
                                    "result": result, "label": component_value.get("label"),
                                }
                            else:
                                st.session_state._diagnosis = {
                                    "clip_id": clip_id, "error": "Clip not found",
                                }
                        else:
                            result = diagnose_missed_rally(
                                clip_start, clip_end,
                                audio_ev_path, act_path, diag_cfg,
                            )
                            st.session_state._diagnosis = {
                                "clip_id": clip_id, "is_auto": False,
                                "result": result, "label": component_value.get("label"),
                            }
                    except Exception as e:
                        st.session_state._diagnosis = {
                            "clip_id": clip_id, "error": str(e),
                        }
                st.rerun()

    # Show single export result
    if "_single_export_result" in st.session_state:
        msg = st.session_state.pop("_single_export_result")
        if msg.startswith("Exported"):
            st.success(msg)
        else:
            st.error(msg)

    # Show diagnosis result
    if "_diagnosis" in st.session_state:
        diag = st.session_state._diagnosis
        diag_label = diag.get("label", f"Clip {diag['clip_id']}")
        with st.expander(f"Clip Diagnosis: {diag_label}", expanded=True):
            if "error" in diag:
                st.warning(diag["error"])
            elif diag.get("is_auto"):
                _render_explanation(diag["result"])
            else:
                _render_missed_diagnosis(diag["result"])
            if st.button("Close Diagnosis", key="close_diag"):
                del st.session_state._diagnosis
                st.rerun()

    # ── Batch highlight controls ──────────────────────────────────────────
    if st.session_state.clips:
        st.divider()

        highlight_count = sum(1 for c in st.session_state.clips if c.get("is_highlight", False))
        total = len(st.session_state.clips)

        col_b1, col_b2 = st.columns(2)
        with col_b1:
            if st.button("Highlight All"):
                for c in st.session_state.clips:
                    c["is_highlight"] = True
                _save_clips(st.session_state.job_path, st.session_state.clips)
                st.rerun()
        with col_b2:
            if st.button("Clear Highlights"):
                for c in st.session_state.clips:
                    c["is_highlight"] = False
                _save_clips(st.session_state.job_path, st.session_state.clips)
                st.rerun()
        st.caption(f"{highlight_count} / {total} rallies highlighted")

    # ── Quick Export from editor ──────────────────────────────────────────
    if st.session_state.clips:
        exp = exports_dir(st.session_state.job_path)

        highlighted_clips = [c for c in display_clips if c.get("is_highlight", False)]

        col_e1, col_e2 = st.columns(2)
        with col_e1:
            if st.button(
                f"Export Highlights ({len(highlighted_clips)})",
                type="primary",
                disabled=len(highlighted_clips) == 0,
            ):
                highlights_data = _to_highlights(highlighted_clips)
                with open(art / "highlights.json", "w", encoding="utf-8") as f:
                    json.dump(highlights_data, f, indent=2)
                try:
                    _run_step("export")
                    st.success("Export complete!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Export failed: {e}")
        with col_e2:
            if st.button(f"Export All Rallies ({len(display_clips)})"):
                highlights_data = _to_highlights(display_clips)
                with open(art / "highlights.json", "w", encoding="utf-8") as f:
                    json.dump(highlights_data, f, indent=2)
                try:
                    _run_step("export")
                    st.success("Export complete!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Export failed: {e}")

        col_v, col_g = st.columns(2)
        with col_v:
            if st.button(
                f"Export Video Only ({len(highlighted_clips)})",
                disabled=len(highlighted_clips) == 0,
            ):
                highlights_data = _to_highlights(highlighted_clips)
                with open(art / "highlights.json", "w", encoding="utf-8") as f:
                    json.dump(highlights_data, f, indent=2)
                try:
                    _run_step("export", {"export": {"export_format": "video"}})
                    st.success("Video export complete!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Export failed: {e}")
        with col_g:
            if st.button(
                f"Export GIF Only ({len(highlighted_clips)})",
                disabled=len(highlighted_clips) == 0,
            ):
                highlights_data = _to_highlights(highlighted_clips)
                with open(art / "highlights.json", "w", encoding="utf-8") as f:
                    json.dump(highlights_data, f, indent=2)
                try:
                    _run_step("export", {"export": {"export_format": "gif"}})
                    st.success("GIF export complete!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Export failed: {e}")

        # Show exported clips inline
        clips_dir = exp / "clips"
        if clips_dir.exists():
            exported_mp4 = sorted(clips_dir.glob("*.mp4"))
            exported_gif = sorted(clips_dir.glob("*.gif"))
            if exported_mp4 or exported_gif:
                with st.expander(f"Exported Clips ({len(exported_mp4)} MP4, {len(exported_gif)} GIF)", expanded=False):
                    for ep in exported_mp4:
                        st.caption(ep.name)
                        st.video(str(ep))
                    if exported_gif:
                        st.subheader("GIFs")
                        for gp in exported_gif:
                            st.caption(gp.name)
                            st.image(str(gp))


# ─── Screen 3: Export ─────────────────────────────────────────────────────────

def _screen_export():
    st.header("3. Export")

    if not _check_job():
        return

    art = artifacts_dir(st.session_state.job_path)
    exp = exports_dir(st.session_state.job_path)
    clips = sorted(st.session_state.clips, key=lambda c: c["clip_start"])

    if not clips:
        st.warning("No clips to export. Go to Clip Editor first.")
        return

    st.subheader("Clip Summary")

    # Export mode: Highlights Only vs All Rallies
    export_mode = st.radio(
        "Export mode", ["Highlights Only", "All Rallies"],
        horizontal=True, key="export_mode",
    )
    if export_mode == "Highlights Only":
        clips = [c for c in clips if c.get("is_highlight", False)]
        if not clips:
            st.info("No highlighted clips. Toggle highlights in Clip Editor.")

    # Select all / deselect all toggle
    all_selected = st.checkbox("Select All", value=True, key="export_select_all")

    selected_clips = []
    for c in clips:
        dur = c["clip_end"] - c["clip_start"]
        star = " ★" if c.get("is_highlight", False) else ""
        label = f"{c['label']}{star} — {c['clip_start']:.1f}s ~ {c['clip_end']:.1f}s ({dur:.1f}s)"
        checked = st.checkbox(label, value=all_selected, key=f"export_clip_{c['id']}")
        if checked:
            selected_clips.append(c)

    st.caption(f"{len(selected_clips)} / {len(clips)} clips selected")

    if not selected_clips:
        st.warning("Export할 클립을 선택하세요.")
        return

    col_all, col_vid, col_gif = st.columns(3)
    with col_all:
        if st.button("Export Selected", type="primary"):
            highlights_data = _to_highlights(selected_clips)
            with open(art / "highlights.json", "w", encoding="utf-8") as f:
                json.dump(highlights_data, f, indent=2)
            try:
                _run_step("export")
                st.success("Export complete!")
                st.rerun()
            except Exception as e:
                st.error(f"Export failed: {e}")
    with col_vid:
        if st.button("Export Video Only"):
            highlights_data = _to_highlights(selected_clips)
            with open(art / "highlights.json", "w", encoding="utf-8") as f:
                json.dump(highlights_data, f, indent=2)
            try:
                _run_step("export", {"export": {"export_format": "video"}})
                st.success("Video export complete!")
                st.rerun()
            except Exception as e:
                st.error(f"Export failed: {e}")
    with col_gif:
        if st.button("Export GIF Only"):
            highlights_data = _to_highlights(selected_clips)
            with open(art / "highlights.json", "w", encoding="utf-8") as f:
                json.dump(highlights_data, f, indent=2)
            try:
                _run_step("export", {"export": {"export_format": "gif"}})
                st.success("GIF export complete!")
                st.rerun()
            except Exception as e:
                st.error(f"Export failed: {e}")

    # Show exported clips
    clips_dir = exp / "clips"
    if clips_dir.exists():
        exported_mp4 = sorted(clips_dir.glob("*.mp4"))
        exported_gif = sorted(clips_dir.glob("*.gif"))
        if exported_mp4:
            st.subheader(f"Exported Clips ({len(exported_mp4)} MP4, {len(exported_gif)} GIF)")
            cols_per_row = 3
            for row_start in range(0, len(exported_mp4), cols_per_row):
                cols = st.columns(cols_per_row)
                for j, col in enumerate(cols):
                    idx = row_start + j
                    if idx >= len(exported_mp4):
                        break
                    with col:
                        st.caption(exported_mp4[idx].name)
                        st.video(str(exported_mp4[idx]))
                        # Show corresponding GIF if exists
                        gif_path = clips_dir / (exported_mp4[idx].stem + ".gif")
                        if gif_path.exists():
                            st.image(str(gif_path), caption="GIF")

    reel_path = exp / "highlights_reel.mp4"
    if reel_path.exists():
        st.subheader("Highlights Reel")
        st.video(str(reel_path))


# ─── Diagnosis Rendering ─────────────────────────────────────────────────────

_PARAM_KEY_MAP = {
    "impact_threshold": "p_impact_threshold",
    "impact_gap_max_sec": "p_gap_max",
    "min_impacts": "p_min_impacts",
    "min_rally_duration_sec": "p_min_duration",
    "activity_min_mean": "p_activity_min",
    "impact_score_floor": "p_impact_threshold",
}


def _render_missed_diagnosis(result) -> None:
    """Render diagnosis for a manually-created clip that was NOT auto-detected."""
    if result.all_passed:
        st.success("All filters passed — this rally should have been detected.")
        return

    blocked_str = ", ".join(result.blocked_by)
    st.error(f"Blocked by: {blocked_str}")

    cols = st.columns([2, 1, 1, 2])
    cols[0].markdown("**Filter**")
    cols[1].markdown("**Actual**")
    cols[2].markdown("**Threshold**")
    cols[3].markdown("**Suggestion**")

    for f in result.filters:
        cols = st.columns([2, 1, 1, 2])
        icon = "+" if f.passed else "-"
        cols[0].markdown(f"`{icon}` {f.name}")
        cols[1].text(str(f.actual))
        cols[2].text(str(f.threshold))
        if f.suggestion is not None and not f.passed:
            cols[3].text(f"{f.param_key} → {f.suggestion}")
        else:
            cols[3].text("—")

    if result.suggestions:
        st.divider()
        if st.button("Apply Suggested Parameters", key="apply_diag_params"):
            for param_key, value in result.suggestions.items():
                widget_key = _PARAM_KEY_MAP.get(param_key)
                if widget_key:
                    st.session_state[widget_key] = value
            # Save suggestion artifact
            _save_parameter_suggestions(result.suggestions, clip_id=None)
            del st.session_state._diagnosis
            st.rerun()


def _render_explanation(result) -> None:
    """Render explanation for an auto-detected clip."""
    st.markdown("**Detection reasons:**")
    for r in result.detection_reasons:
        st.markdown(f"- {r}")

    st.divider()
    st.markdown("**Highlight score breakdown:**")

    bd = result.score_breakdown
    formula = (
        f"`{result.combined_score:.2f}` = "
        f"audio({bd['conf_audio']:.2f} x 0.45) + "
        f"video({bd['conf_video_norm']:.2f} x 0.35) + "
        f"rhythm({bd['rhythm_score']:.2f} x 0.20)"
    )
    st.markdown(formula)

    if result.is_highlight:
        st.success(
            f"Highlighted: {result.combined_score:.2f} >= {result.threshold}"
        )
    else:
        st.info(
            f"Not highlighted: {result.combined_score:.2f} < {result.threshold}"
        )

    if result.suggestions:
        st.divider()
        st.markdown("**Suggestions:**")
        for s in result.suggestions:
            st.markdown(f"- {s}")


# ─── ROI Canvas Editor ────────────────────────────────────────────────────────

def _roi_canvas_editor(art: Path, frame0_path: Path) -> None:
    """ROI editor — click 4 points on image for table, inputs for scoreboard."""
    import base64

    roi_path = art / "table_roi.json"
    sb_path = art / "scoreboard_roi.json"

    # Load current polygon
    current_pts = []
    if roi_path.exists():
        with open(roi_path, "r") as f:
            current_roi = json.load(f)
        current_pts = current_roi.get("table_polygon", [])

    # Read image dimensions + encode as base64 data URL
    with open(str(frame0_path), "rb") as f:
        img_bytes = f.read()
    img_b64 = "data:image/jpeg;base64," + base64.b64encode(img_bytes).decode()

    from PIL import Image
    img = Image.open(str(frame0_path))
    img_w, img_h = img.size

    with st.expander("Edit Table ROI (4-point click)", expanded=True):
        st.caption("이미지에서 테이블 꼭짓점 4개를 클릭하세요 (순서 무관, 자동 정렬됩니다)")

        # ROI picker component
        picker_value = _roi_picker_component(
            image_b64=img_b64,
            initial_points=current_pts,
            key="roi_picker",
            height=int(img_h * min(800, img_w) / img_w) + 60,
        )

        # Action buttons
        col_u1, col_u2, col_u3 = st.columns(3)
        with col_u1:
            if st.button("Use Auto Proposal", key="use_auto_table"):
                try:
                    _run_step("setup")
                    st.success("Auto proposal applied!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Auto-detect failed: {e}")
        with col_u2:
            save_clicked = st.button(
                "Save Table ROI", key="save_manual_table", type="primary",
            )
        with col_u3:
            if current_pts:
                st.caption(f"Current: {current_pts}")

        if save_clicked:
            points = None
            if (picker_value and isinstance(picker_value, dict)
                    and picker_value.get("complete")):
                points = picker_value["points"]
            elif current_pts and len(current_pts) == 4:
                points = current_pts

            if points and len(points) == 4:
                from tt_highlights.steps.table_roi import _order_points_clockwise
                points = _order_points_clockwise(points)
                points = [[int(p[0]), int(p[1])] for p in points]
                roi_data = {
                    "table_polygon": points,
                    "polygon_order": "clockwise",
                    "source": "manual",
                    "confidence": 1.0,
                    "frame_id": 0,
                    "frame_size": {"w": img_w, "h": img_h},
                }
                with open(roi_path, "w", encoding="utf-8") as f:
                    json.dump(roi_data, f, indent=2)
                _mark_setup_complete(art)
                _regenerate_overlay(frame0_path, points, art)
                st.success("Table ROI saved!")
                st.rerun()
            else:
                st.warning("4개 점을 모두 클릭한 뒤 저장하세요.")

    with st.expander("Edit Scoreboard ROI (4-point click)", expanded=False):
        current_sb_pts = []
        sb_enabled = False
        if sb_path.exists():
            with open(sb_path, "r") as f:
                sb_info = json.load(f)
            if sb_info.get("enabled"):
                sb_enabled = True
                # Load polygon points if available, else derive from rect
                if sb_info.get("polygon"):
                    current_sb_pts = sb_info["polygon"]
                else:
                    r = sb_info.get("rect", {})
                    if r.get("w", 0) > 0 and r.get("h", 0) > 0:
                        x, y, w, h = r["x"], r["y"], r["w"], r["h"]
                        current_sb_pts = [
                            [x, y], [x + w, y], [x + w, y + h], [x, y + h],
                        ]

        sb_enable = st.checkbox("Enable scoreboard ROI", value=sb_enabled, key="sb_enable")

        if sb_enable:
            sb_picker = _roi_picker_component(
                image_b64=img_b64,
                initial_points=current_sb_pts,
                max_points=4,
                key="sb_roi_picker",
                height=int(img_h * min(800, img_w) / img_w) + 60,
            )

            if st.button("Save Scoreboard ROI", key="save_scoreboard", type="primary"):
                pts = None
                if (sb_picker and isinstance(sb_picker, dict)
                        and sb_picker.get("complete")):
                    pts = sb_picker["points"]
                elif current_sb_pts and len(current_sb_pts) == 4:
                    pts = current_sb_pts

                if pts and len(pts) == 4:
                    x_min = min(p[0] for p in pts)
                    y_min = min(p[1] for p in pts)
                    x_max = max(p[0] for p in pts)
                    y_max = max(p[1] for p in pts)
                    sb_data = {
                        "enabled": True,
                        "polygon": pts,
                        "rect": {
                            "x": x_min, "y": y_min,
                            "w": x_max - x_min, "h": y_max - y_min,
                        },
                        "source": "manual",
                        "confidence": 1.0,
                        "frame_id": 0,
                    }
                    with open(sb_path, "w", encoding="utf-8") as f:
                        json.dump(sb_data, f, indent=2)
                    st.success("Scoreboard ROI saved!")
                    st.rerun()
                else:
                    st.warning("4개 점을 모두 클릭한 뒤 저장하세요.")
        else:
            if sb_enabled:
                if st.button("Disable Scoreboard ROI", key="disable_scoreboard"):
                    sb_data = {
                        "enabled": False,
                        "rect": {"x": 0, "y": 0, "w": 0, "h": 0},
                        "source": "none",
                        "confidence": 0.0,
                        "frame_id": 0,
                    }
                    with open(sb_path, "w", encoding="utf-8") as f:
                        json.dump(sb_data, f, indent=2)
                    st.success("Scoreboard ROI disabled.")
                    st.rerun()

    # ── Player Zone Editor ────────────────────────────────────────────────────
    _player_zone_editor(art, frame0_path, img_w, img_h)


def _player_zone_editor(
    art: Path, frame0_path: Path, img_w: int, img_h: int,
) -> None:
    """Player zone manual editor — auto-derive or number-input edit."""
    import cv2

    pz_path = art / "player_zones.json"
    roi_path = art / "table_roi.json"

    # Load current zones
    current_zones = []
    pz_source = "none"
    if pz_path.exists():
        with open(pz_path, "r", encoding="utf-8") as f:
            pz_data = json.load(f)
        current_zones = pz_data.get("zones", [])
        pz_source = pz_data.get("source", "none")

    with st.expander("Edit Player Zones", expanded=False):
        pz_enable = st.checkbox(
            "Enable player zones",
            value=len(current_zones) > 0,
            key="pz_enable",
        )

        if not pz_enable:
            if current_zones:
                if st.button("Clear Player Zones", key="clear_pz"):
                    if pz_path.exists():
                        pz_path.unlink()
                    st.success("Player zones cleared.")
                    st.rerun()
            return

        # Auto-derive button
        if st.button("Auto-derive from Table ROI", key="auto_derive_pz"):
            if roi_path.exists():
                with open(roi_path, "r", encoding="utf-8") as f:
                    roi_data = json.load(f)
                polygon = roi_data.get("table_polygon", [])
                if polygon and len(polygon) == 4:
                    from tt_highlights.steps.setup import _auto_derive_zones
                    cfg = load_config(st.session_state.job_path)
                    pz_cfg = cfg.get("player_zones", {})
                    zones = _auto_derive_zones(polygon, img_h, img_w, pz_cfg)
                    pz_out = {
                        "source": "auto",
                        "zones": zones,
                        "player_a_score_side": "left",
                        "frame_size": {"w": img_w, "h": img_h},
                    }
                    with open(pz_path, "w", encoding="utf-8") as f:
                        json.dump(pz_out, f, indent=2)
                    st.success("Player zones auto-derived!")
                    st.rerun()
                else:
                    st.warning("Table ROI에 4개 점이 필요합니다. 먼저 Table ROI를 설정하세요.")
            else:
                st.warning("Table ROI가 없습니다. 먼저 Table ROI를 설정하세요.")

        st.caption(f"Source: {pz_source}")

        # Load current polygon points per zone
        zone_map = {z["label"]: z for z in current_zones}

        # Prepare image for pickers
        import base64
        with open(frame0_path, "rb") as fimg:
            img_b64 = "data:image/jpeg;base64," + base64.b64encode(fimg.read()).decode()
        # Use generous height; component's setFrameHeight will adjust
        picker_h = max(int(img_h * 800 / max(img_w, 1)) + 80, 500)

        # ── Near zone picker ──
        st.markdown("**Near zone** (카메라 쪽 선수) — 4점 클릭")
        near_zone = zone_map.get("near", {})
        near_init = near_zone.get("polygon", [])
        near_picker = _roi_picker_component(
            image_b64=img_b64,
            initial_points=near_init,
            max_points=4,
            key="pz_near_picker",
            height=picker_h,
        )

        # ── Far zone picker ──
        st.markdown("**Far zone** (반대편 선수) — 4점 클릭")
        far_zone = zone_map.get("far", {})
        far_init = far_zone.get("polygon", [])
        far_picker = _roi_picker_component(
            image_b64=img_b64,
            initial_points=far_init,
            max_points=4,
            key="pz_far_picker",
            height=picker_h,
        )

        if st.button("Save Player Zones", key="save_pz", type="primary"):
            from tt_highlights.steps.table_roi import _order_points_clockwise

            def _build_zone(label, picker_val, init_pts):
                pts = None
                if (picker_val and isinstance(picker_val, dict)
                        and picker_val.get("complete")):
                    pts = picker_val["points"]
                elif init_pts and len(init_pts) == 4:
                    pts = init_pts
                if not pts or len(pts) != 4:
                    return None
                pts = _order_points_clockwise(pts)
                pts = [[int(p[0]), int(p[1])] for p in pts]
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                return {
                    "label": label,
                    "polygon": pts,
                    "rect": {
                        "x": min(xs), "y": min(ys),
                        "w": max(xs) - min(xs), "h": max(ys) - min(ys),
                    },
                    "edge_pts": [],
                }

            near_out = _build_zone("near", near_picker, near_init)
            far_out = _build_zone("far", far_picker, far_init)

            if near_out and far_out:
                pz_out = {
                    "source": "manual",
                    "zones": [near_out, far_out],
                    "player_a_score_side": "left",
                    "frame_size": {"w": img_w, "h": img_h},
                }
                with open(pz_path, "w", encoding="utf-8") as f:
                    json.dump(pz_out, f, indent=2)
                st.success("Player zones saved!")
                st.rerun()
            else:
                st.warning("Near, Far 각각 4개 점을 모두 클릭하세요.")


def _draw_zone_overlay(frame0_path: Path, zones: list[dict], out_path: Path) -> None:
    """Draw zone polygons (or rectangles) on frame0 for preview."""
    import cv2
    import numpy as np

    frame = cv2.imread(str(frame0_path))
    if frame is None:
        return
    colors = {"near": (255, 0, 0), "far": (0, 0, 255)}
    for z in zones:
        c = colors.get(z.get("label", ""), (0, 255, 0))
        poly = z.get("polygon")
        if poly and len(poly) >= 3:
            pts = np.array(poly, dtype=np.int32)
            overlay = frame.copy()
            cv2.fillPoly(overlay, [pts], c)
            frame = cv2.addWeighted(overlay, 0.2, frame, 0.8, 0)
            cv2.polylines(frame, [pts], True, c, 2)
            cv2.putText(frame, z.get("label", ""), (pts[0][0] + 5, pts[0][1] + 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, c, 2)
        else:
            r = z.get("rect", {})
            if r.get("w", 0) <= 0 or r.get("h", 0) <= 0:
                continue
            overlay = frame.copy()
            cv2.rectangle(overlay, (r["x"], r["y"]),
                          (r["x"] + r["w"], r["y"] + r["h"]), c, -1)
            frame = cv2.addWeighted(overlay, 0.2, frame, 0.8, 0)
            cv2.rectangle(frame, (r["x"], r["y"]),
                          (r["x"] + r["w"], r["y"] + r["h"]), c, 2)
            cv2.putText(frame, z.get("label", ""), (r["x"] + 5, r["y"] + 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, c, 2)
    cv2.imwrite(str(out_path), frame)


def _regenerate_overlay(frame0_path: Path, polygon: list, art: Path) -> None:
    """Regenerate the debug overlay image with new polygon."""
    import cv2
    from tt_highlights.steps.table_roi import _draw_overlay
    dbg = art.parent / "debug"
    dbg.mkdir(parents=True, exist_ok=True)
    frame = cv2.imread(str(frame0_path))
    if frame is not None:
        _draw_overlay(frame, polygon, dbg / "frame0_overlay.png")


def _save_parameter_suggestions(suggestions: dict, clip_id: int | None) -> None:
    """Persist parameter suggestions to artifact file."""
    from datetime import datetime, timezone
    art = artifacts_dir(st.session_state.job_path)
    artifact = {
        "suggestions": suggestions,
        "source_clip_id": clip_id,
        "applied": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(art / "parameter_suggestions.json", "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2)


def _load_parameter_suggestions() -> dict | None:
    """Load saved parameter suggestions if they exist."""
    art = artifacts_dir(st.session_state.job_path)
    path = art / "parameter_suggestions.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _record_feedback_label(
    clip_id: int, rally_id: int | None, action: str,
) -> None:
    """Append a feedback label entry to feedback_labels.json."""
    from datetime import datetime, timezone
    art = artifacts_dir(st.session_state.job_path)
    path = art / "feedback_labels.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"labels": []}

    data["labels"].append({
        "clip_id": clip_id,
        "rally_id": rally_id,
        "action": action,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _load_setup_state(art: Path) -> dict | None:
    """Load setup_state.json if it exists."""
    path = art / "setup_state.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _mark_setup_complete(art: Path) -> None:
    """Write setup_state.json marking setup as complete."""
    from datetime import datetime, timezone
    state = {
        "completed": True,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "requires_review": False,
        "warnings": [],
    }
    with open(art / "setup_state.json", "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _check_job() -> bool:
    if not st.session_state.job_path:
        st.warning("No job loaded. Go to Setup first.")
        return False
    return True


def _load_job_config() -> dict:
    jp = Path(st.session_state.job_path)
    config_path = jp.parent / "config.yaml"
    return load_config(str(config_path))


def _export_single_clip(
    clip_start: float, clip_end: float, label: str, fmt: str,
) -> Path:
    """Export a single clip as MP4 or GIF without touching other exports."""
    job = load_job(st.session_state.job_path)
    input_video = job["input_video"]
    exp = exports_dir(st.session_state.job_path)
    clips_dir = exp / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    safe_label = label.replace(" ", "_").replace("/", "_")
    duration = clip_end - clip_start

    if fmt == "video":
        from tt_highlights.runtime import get_video_encoder
        config = _load_job_config()
        venc_args = get_video_encoder(config)
        out_path = clips_dir / f"{safe_label}.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(clip_start), "-i", input_video, "-t", str(duration),
            *venc_args,
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(out_path),
        ]
    else:
        config = _load_job_config()
        export_cfg = config.get("export", {})
        gif_width = export_cfg.get("gif_width", 640)
        gif_fps = export_cfg.get("gif_fps", 20)
        out_path = clips_dir / f"{safe_label}.gif"
        vf = (f"fps={gif_fps},scale={gif_width}:-1:flags=lanczos,"
              f"split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(clip_start), "-i", input_video, "-t", str(duration),
            "-vf", vf, "-loop", "0",
            str(out_path),
        ]

    with st.spinner(f"Exporting {out_path.name}..."):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {result.stderr[:200]}")
    return out_path


def _run_step(step_name: str, config_override: dict | None = None):
    job = load_job(st.session_state.job_path)
    config = _load_job_config()
    if config_override:
        from tt_highlights.config import _deep_merge
        config = _deep_merge(config, config_override)
    step_fn = get_step_function(step_name)
    with st.spinner(f"Running {step_name}..."):
        step_fn(job, config, st.session_state.job_path)


def _next_clip_id() -> int:
    if not st.session_state.clips:
        return 1
    return max(c["id"] for c in st.session_state.clips) + 1


def _save_clips(job_path: str, clips: list):
    art = artifacts_dir(job_path)
    with open(art / "manual_clips.json", "w", encoding="utf-8") as f:
        json.dump(clips, f, indent=2, ensure_ascii=False)


def _load_clips(job_path: str) -> list:
    art = artifacts_dir(job_path)
    clips_path = art / "manual_clips.json"
    if clips_path.exists():
        with open(clips_path, "r", encoding="utf-8") as f:
            clips = json.load(f)
        if clips:
            # Backward compat: default is_highlight to False for old data
            for c in clips:
                if "is_highlight" not in c:
                    c["is_highlight"] = False
            return clips

    # Fallback: load from rallies.json if manual_clips is missing/empty
    rallies_path = art / "rallies.json"
    if rallies_path.exists():
        with open(rallies_path, "r", encoding="utf-8") as f:
            rallies_data = json.load(f)
        rallies = rallies_data.get("rallies", [])
        if rallies:
            clips = []
            for r in rallies:
                end = r.get("end_refined", r["end"])
                clips.append({
                    "id": r["id"],
                    "rally_id": r["id"],
                    "clip_start": r["start"],
                    "clip_end": end,
                    "label": f"Rally {r['id']}",
                    "is_highlight": False,
                    "conf_audio": r.get("conf_audio", 0),
                    "conf_video": r.get("conf_video", 0),
                    "impact_count": r.get("impact_count", 0),
                    "rhythm_score": r.get("rhythm_score", 0),
                    "reason_end": r.get("reason_end_refined", ""),
                })
            # Save as manual_clips so next load is instant
            _save_clips(job_path, clips)
            return clips

    return []


def _to_highlights(clips: list) -> dict:
    sorted_clips = sorted(clips, key=lambda c: c["clip_start"])
    return {
        "clip_mode": "manual",
        "highlights": [
            {
                "rank": i + 1,
                "category": c.get("label", "manual"),
                "rally_id": c["id"],
                "clip_start": c["clip_start"],
                "clip_end": c["clip_end"],
                "score": 0.0,
                "reasons": ["manual"],
            }
            for i, c in enumerate(sorted_clips)
        ],
    }


def _extract_preview_clip(
    input_path: str, start: float, end: float, output_path: str,
):
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-to", str(end),
        "-i", input_path,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-c:a", "aac", "-b:a", "128k",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[:200]}")


# ─── Step override filtering ──────────────────────────────────────────────────

_STEP_OVERRIDE_KEYS = {
    "audio_events": {"audio"},
    "video_activity": {"video"},
    "rally_segment": {"segmentation"},
    "scoreboard_ocr": {"ocr"},
    "ball_tracking": {"ball"},
    "player_motion": {"player_motion"},
    "pose_estimation": {"pose_estimation"},
    "features": set(),
    "scoring": set(),
}


def _filter_override_for_step(config_override: dict, step_name: str) -> dict:
    """Return only the override keys relevant to a specific step."""
    allowed_keys = _STEP_OVERRIDE_KEYS.get(step_name, set())
    if not allowed_keys:
        return {}
    return {k: v for k, v in config_override.items() if k in allowed_keys}


# ─── Inspector Panels ─────────────────────────────────────────────────────────


def _render_inspector_panels(rally_id: int, art: Path) -> None:
    """Render Debug Panel, Rally Inspector, and Calibration Mode for a rally."""
    from tt_highlights.inspector import (
        load_rally_inspector, PoseStatus, OcrStatus,
    )

    inspector_data = load_rally_inspector(st.session_state.job_path, rally_id)
    _render_debug_panel(inspector_data, PoseStatus, OcrStatus)

    with st.expander("Rally Inspector", expanded=False):
        _render_rally_inspector(inspector_data, rally_id, art, PoseStatus)

    with st.expander("Calibration Mode", expanded=False):
        _render_calibration_mode(rally_id, art)


def _render_debug_panel(data: dict, PoseStatus, OcrStatus) -> None:
    """Render the quick-glance debug panel with metrics and warnings."""
    summary = data.get("summary")
    if summary is None:
        st.warning("No rally data available")
        return

    st.subheader(f"Debug Panel \u2014 Rally {summary['rally_id']}")

    # Row 1: key metrics
    pose = data.get("pose")
    ocr = data.get("ocr", {})
    scores = data.get("scores")

    pose_status_val = "N/A"
    if pose:
        ps = pose["status"]
        pose_status_val = ps.value if isinstance(ps, PoseStatus) else str(ps)
    ocr_status_val = ocr.get("status", OcrStatus.UNAVAILABLE)
    if isinstance(ocr_status_val, OcrStatus):
        ocr_status_val = ocr_status_val.value

    top_cat = scores["top_category"] if scores else "N/A"
    seg_score = (
        f"{summary['segment_score']:.2f}"
        if summary.get("segment_score") is not None else "N/A"
    )
    swing_diff = f"{pose['swing_count_diff']:.0f}" if pose else "N/A"

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Rally ID", summary["rally_id"])
    c2.metric("Segment Score", seg_score)
    c3.metric("Top Category", top_cat)
    c4.metric("Pose Status", pose_status_val)
    c5.metric("OCR Status", ocr_status_val)
    c6.metric("Swing Count Diff", swing_diff)

    # Row 2: motion/pose details
    motion = data.get("motion")
    near_mean = f"{motion['near']['raw_mean']:.4f}" if motion else "N/A"
    far_mean = f"{motion['far']['raw_mean']:.4f}" if motion else "N/A"
    wrist_peak = (
        f"{pose['near']['wrist_speed_peak']:.4f}"
        if pose and pose.get("near") else "N/A"
    )
    pose_asym = f"{pose['asymmetry']:.4f}" if pose else "N/A"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Near Motion Mean", near_mean)
    c2.metric("Far Motion Mean", far_mean)
    c3.metric("Near Wrist Speed Peak", wrist_peak)
    c4.metric("Pose Asymmetry", pose_asym)

    # Row 3: top reasons
    if scores and scores.get("top_reasons"):
        st.markdown("**Top Reasons:** " + ", ".join(scores["top_reasons"]))

    # Row 4: per-step freshness badges (compact one-line view)
    freshness = data.get("freshness", {})
    artifacts_info = freshness.get("artifacts", {})
    if artifacts_info:
        badges = []
        for name, info in artifacts_info.items():
            if not info["exists"]:
                badges.append(f"`{name}` missing")
            elif info["stale"]:
                badges.append(f"`{name}` stale")
            else:
                badges.append(f"`{name}` ok")
        st.markdown("**Artifact status:** " + " | ".join(badges))

    # Row 5: freshness warnings (details)
    for w in freshness.get("warnings", []):
        st.warning(w)

    # Row 6: status messages (skip duplicates from freshness)
    freshness_warnings = set(freshness.get("warnings", []))
    for msg in data.get("status_messages", []):
        if msg not in freshness_warnings:
            st.info(msg)


def _render_rally_inspector(data: dict, rally_id: int, art: Path, PoseStatus) -> None:
    """Render the detailed Rally Inspector panel."""
    summary = data.get("summary")
    if summary is None:
        st.warning("No rally data")
        return

    # ── Summary table ──
    st.subheader("Summary")
    summary_rows = {
        "Start (s)": summary["start"],
        "End (s)": summary["end"],
        "Duration (s)": summary["duration"],
        "Impact Count": summary["impact_count"],
        "Impact Rate (/s)": summary["impact_rate"],
        "Impact Peak": summary["impact_peak"],
        "Activity Mean": summary["activity_mean"],
        "Activity Peak": summary["activity_peak"],
    }

    motion = data.get("motion")
    if motion:
        summary_rows.update({
            "Near Motion Mean": motion["near"]["raw_mean"],
            "Far Motion Mean": motion["far"]["raw_mean"],
            "Near End Burst": motion["near"]["raw_end_burst"],
            "Far End Burst": motion["far"]["raw_end_burst"],
            "Motion Asymmetry": motion["asymmetry"],
        })

    pose = data.get("pose")
    if pose and pose.get("near"):
        summary_rows.update({
            "Near Wrist Speed Peak": pose["near"]["wrist_speed_peak"],
            "Far Wrist Speed Peak": pose.get("far", {}).get("wrist_speed_peak", 0),
            "Near Arm Extension Peak": pose["near"]["arm_extension_peak"],
            "Pose Asymmetry": pose["asymmetry"],
            "Swing Count Diff": pose["swing_count_diff"],
        })

    ocr = data.get("ocr", {})
    ocr_status = ocr.get("status")
    if hasattr(ocr_status, "value"):
        ocr_status = ocr_status.value
    summary_rows["OCR Status"] = ocr_status or "N/A"

    import pandas as pd
    df = pd.DataFrame(
        [{"Metric": k, "Value": v} for k, v in summary_rows.items()]
    )
    st.dataframe(df, use_container_width=True, hide_index=True)

    # ── Score Breakdown ──
    scores = data.get("scores")
    if scores and scores.get("categories"):
        st.subheader("Score Breakdown")
        _render_score_breakdown(scores)

    # ── Event Timeline ──
    events = data.get("events")
    if events:
        st.subheader("Event Timeline")
        _render_event_timeline(events, summary["start"], summary["end"])

    # ── Rally Frame Preview (with skeleton insets) ──
    st.subheader("Rally Frame Preview")
    _render_rally_frames(rally_id, art, summary, count=3, include_skeletons=True)


def _render_score_breakdown(scores: dict) -> None:
    """Render a bar chart of score contributions per category."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    categories = scores.get("categories", {})
    if not categories:
        return

    fig, axes = plt.subplots(
        1, len(categories), figsize=(5 * len(categories), 4),
    )
    if len(categories) == 1:
        axes = [axes]

    for ax, (cat, info) in zip(axes, categories.items()):
        reasons = info.get("reasons", [])
        if not reasons:
            ax.set_title(f"{cat}: {info['score']:.2f}")
            continue

        features = [r.get("feature", "?") for r in reasons]
        contribs = [r.get("contribution", 0) for r in reasons]
        colors = ["#4CAF50" if c > 0 else "#F44336" for c in contribs]

        ax.barh(features, contribs, color=colors, alpha=0.8)
        ax.set_title(f"{cat}: {info['score']:.2f}")
        ax.set_xlabel("Contribution")

    plt.tight_layout()
    st.pyplot(fig)
    plt.close(fig)


def _render_event_timeline(
    events: dict, rally_start: float, rally_end: float,
) -> None:
    """Render an event timeline plot for one rally."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(14, 2.5))

    # Rally bounds
    ax.axvline(rally_start, color="green", linestyle="-", linewidth=2,
               label="Rally bounds")
    ax.axvline(rally_end, color="green", linestyle="-", linewidth=2)

    # Impacts
    for imp in events.get("impacts", []):
        ax.axvline(imp["t"], color="red", linestyle=":", alpha=0.5, linewidth=1)
    if events.get("impacts"):
        # Re-draw first one with label for legend
        ax.axvline(events["impacts"][0]["t"], color="red", linestyle=":",
                    alpha=0.5, label="Impacts")

    # OCR events
    for ocr in events.get("ocr", []):
        ax.axvline(ocr["t"], color="blue", linestyle="-", alpha=0.6,
                    linewidth=1.5)
    if events.get("ocr"):
        ax.axvline(events["ocr"][0]["t"], color="blue", linestyle="-",
                    alpha=0.6, label="OCR event")

    # Cheer segments
    for ch in events.get("cheers", []):
        ax.axvspan(ch["start"], ch["end"], color="gold", alpha=0.2)
    if events.get("cheers"):
        ax.axvspan(events["cheers"][0]["start"], events["cheers"][0]["end"],
                    color="gold", alpha=0.2, label="Cheer")

    ax.set_xlim(rally_start - 0.5, rally_end + 0.5)
    ax.set_xlabel("Time (s)")
    ax.set_yticks([])
    ax.legend(loc="upper right", fontsize=8)
    ax.set_title("Event Timeline")
    plt.tight_layout()
    st.pyplot(fig)
    plt.close(fig)


def _render_rally_frames(
    rally_id: int, art: Path, summary: dict, count: int,
    include_skeletons: bool = True,
) -> None:
    """Extract and display rally frames with zone + skeleton overlays."""
    from tt_highlights.inspector import (
        extract_rally_frames, list_pose_debug_samples,
    )

    dbg = debug_dir(st.session_state.job_path)
    proxy_path = art / "proxy.mp4"
    if not proxy_path.exists():
        st.caption("proxy.mp4 not found")
        return

    # Load zone and geometry data
    zones = None
    zones_path = art / "player_zones.json"
    if zones_path.exists():
        with open(zones_path, "r") as f:
            zones_data = json.load(f)
        zones = zones_data.get("zones")

    table_polygon = None
    roi_path = art / "table_roi.json"
    if roi_path.exists():
        with open(roi_path, "r") as f:
            roi_data = json.load(f)
        table_polygon = roi_data.get("table_polygon")

    scoreboard_rect = None
    sb_path = art / "scoreboard_roi.json"
    if sb_path.exists():
        with open(sb_path, "r") as f:
            sb_data = json.load(f)
        scoreboard_rect = sb_data.get("rect")

    # Load skeleton samples if requested (composited as insets on frames)
    skeleton_samples = None
    if include_skeletons:
        skeleton_samples = list_pose_debug_samples(
            st.session_state.job_path, rally_id,
        ) or None

    frames = extract_rally_frames(
        proxy_path=proxy_path,
        rally_start=summary["start"],
        rally_end=summary["end"],
        rally_id=rally_id,
        count=count,
        cache_dir=dbg / "inspector_frames",
        zones=zones,
        table_polygon=table_polygon,
        scoreboard_rect=scoreboard_rect,
        skeleton_samples=skeleton_samples,
    )

    if frames:
        cols = st.columns(len(frames))
        for i, fr in enumerate(frames):
            with cols[i]:
                st.image(str(fr["path"]), caption=f"t={fr['t']:.1f}s")
        if skeleton_samples:
            st.caption("Skeleton insets shown in zone corners (nearest-time match)")
    else:
        st.caption("Could not extract frames")


def _render_calibration_mode(rally_id: int, art: Path) -> None:
    """Render the Calibration Mode panel."""
    from tt_highlights.inspector import build_calibration_series

    cal_data = build_calibration_series(st.session_state.job_path, rally_id)

    # Pose valid frame ratio caption
    st.caption(
        f"Pose valid frames: near {cal_data['near_pose_valid_ratio']:.0%}, "
        f"far {cal_data['far_pose_valid_ratio']:.0%}"
    )

    # Time-series plot
    _render_calibration_plot(cal_data)

    # Rally Frame Preview (5 frames)
    st.subheader("Rally Frame Preview")
    rallies_data = None
    rallies_path = art / "rallies.json"
    if rallies_path.exists():
        with open(rallies_path, "r") as f:
            rallies_data = json.load(f)

    summary_info = None
    if rallies_data:
        for r in rallies_data.get("rallies", []):
            if r["id"] == rally_id:
                summary_info = {
                    "start": r["start"],
                    "end": r.get("end_refined", r["end"]),
                }
                break

    if summary_info:
        _render_rally_frames(rally_id, art, summary_info, count=5)

    # Parameter readback
    st.subheader("Current Parameters")
    st.json(cal_data["params"])


def _render_calibration_plot(cal_data: dict) -> None:
    """Render the multi-signal calibration time-series plot."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rally_start = cal_data["rally_window"]["start"]
    rally_end = cal_data["rally_window"]["end"]

    fig, ax1 = plt.subplots(figsize=(14, 5))
    ax2 = ax1.twinx()

    # Left Y (0-1): activity + motion
    activity = cal_data.get("activity", [])
    if activity:
        at = [s["t"] for s in activity]
        av = [s["value"] for s in activity]
        ax1.plot(at, av, "k-", label="Activity", alpha=0.5, linewidth=0.8)

    near_motion = cal_data.get("near_motion", [])
    if near_motion:
        mt = [s["t"] for s in near_motion]
        mv = [s["value"] for s in near_motion]
        ax1.plot(mt, mv, "b-", label="Near Motion", linewidth=1)

    far_motion = cal_data.get("far_motion", [])
    if far_motion:
        mt = [s["t"] for s in far_motion]
        mv = [s["value"] for s in far_motion]
        ax1.plot(mt, mv, "r-", label="Far Motion", linewidth=1)

    ax1.set_ylabel("Activity / Motion")
    ax1.set_xlabel("Time (s)")

    # Right Y: pose wrist speed curves
    near_pose = cal_data.get("near_pose", [])
    if near_pose:
        pt = [s["t"] for s in near_pose]
        pv = [s["wrist_speed"] for s in near_pose]
        ax2.plot(pt, pv, "b--", label="Near Wrist Speed", alpha=0.7,
                 linewidth=1)

    far_pose = cal_data.get("far_pose", [])
    if far_pose:
        pt = [s["t"] for s in far_pose]
        pv = [s["wrist_speed"] for s in far_pose]
        ax2.plot(pt, pv, "r--", label="Far Wrist Speed", alpha=0.7,
                 linewidth=1)

    ax2.set_ylabel("Wrist Speed (normalized)")

    # Pose missing-gap shading (use actual sample fps from data)
    pose_fps = cal_data.get("pose_sample_fps", 5)
    if near_pose:
        _shade_pose_gaps(ax1, [s["t"] for s in near_pose],
                         rally_start, rally_end, color="blue", alpha=0.05,
                         pose_sample_fps=pose_fps)
    if far_pose:
        _shade_pose_gaps(ax1, [s["t"] for s in far_pose],
                         rally_start, rally_end, color="red", alpha=0.05,
                         pose_sample_fps=pose_fps)
    if not near_pose and not far_pose:
        ax1.axvspan(rally_start, rally_end, color="gray", alpha=0.1)

    # Vertical markers
    for imp in cal_data.get("impacts", []):
        ax1.axvline(imp["t"], color="red", linestyle=":", alpha=0.4,
                     linewidth=0.8)

    ocr_event = cal_data.get("ocr_event")
    if ocr_event:
        ax1.axvline(ocr_event["t"], color="blue", linestyle="-", alpha=0.6,
                     linewidth=1.5)

    # Rally bounds
    ax1.axvline(rally_start, color="green", linestyle="-", linewidth=1.5)
    ax1.axvline(rally_end, color="green", linestyle="-", linewidth=1.5)

    # Cheer segments
    for ch in cal_data.get("cheers", []):
        ax1.axvspan(ch["start"], ch["end"], color="gold", alpha=0.15)

    # Combined legend
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2,
               loc="upper left", fontsize=8)

    ax1.set_title("Calibration: Activity + Motion + Pose")
    plt.tight_layout()
    st.pyplot(fig)
    plt.close(fig)


def _shade_pose_gaps(ax, pose_times, rally_start, rally_end, color, alpha,
                     pose_sample_fps=5):
    """Shade time gaps where pose detection was missing."""
    if not pose_times:
        ax.axvspan(rally_start, rally_end, color="gray", alpha=0.1)
        return
    expected_interval = 1.0 / max(pose_sample_fps, 1)
    gap_threshold = expected_interval * 3
    # Gap at start
    if pose_times[0] - rally_start > gap_threshold:
        ax.axvspan(rally_start, pose_times[0], color=color, alpha=alpha)
    # Inter-sample gaps
    for i in range(1, len(pose_times)):
        if pose_times[i] - pose_times[i - 1] > gap_threshold:
            ax.axvspan(pose_times[i - 1], pose_times[i],
                        color=color, alpha=alpha)
    # Gap at end
    if rally_end - pose_times[-1] > gap_threshold:
        ax.axvspan(pose_times[-1], rally_end, color=color, alpha=alpha)


main()
