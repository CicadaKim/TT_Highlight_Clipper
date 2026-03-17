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
                st.success("Job created and preprocessed!")
                st.session_state.current_screen = "editor"
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
    if setup_done:
        st.success("Setup complete — ROI detected.")
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

    # Gate: setup must be complete before auto-detect
    if not is_setup_complete(st.session_state.job_path):
        st.warning("ROI setup not completed. Go to Setup screen and run ROI detection first.")

    if st.button("Auto-detect Rallies", type="secondary",
                 disabled=not is_setup_complete(st.session_state.job_path)):
        try:
            with st.spinner("Running audio event detection..."):
                _run_step("audio_events", config_override)
            with st.spinner("Running video activity analysis..."):
                _run_step("video_activity", config_override)
            with st.spinner("Running rally segmentation..."):
                _run_step("rally_segment", config_override)

            # Immediately add detected rallies to clips
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
                        ca = r.get("conf_audio", 0)
                        cv_norm = r.get("conf_video_norm", 0)
                        rhythm = r.get("rhythm_score", 0)
                        cv_raw = r.get("conf_video", 0)
                        vid_floor = hl_cfg.get("video_floor", 0.03)
                        if cv_raw < vid_floor:
                            combined = 0.0
                        else:
                            combined = ca * 0.3 + cv_norm * 0.5 + rhythm * 0.2
                        st.session_state.clips.append({
                            "id": next_id,
                            "rally_id": r["id"],
                            "clip_start": r["start"],
                            "clip_end": end,
                            "label": f"Rally {r['id']}",
                            "is_highlight": combined >= hl_threshold,
                            "conf_audio": r.get("conf_audio", 0),
                            "conf_video": r.get("conf_video", 0),
                            "conf_video_norm": cv_norm,
                            "impact_count": r.get("impact_count", 0),
                            "rhythm_score": r.get("rhythm_score", 0),
                            "reason_end": r.get("reason_end_refined", ""),
                        })
                        next_id += 1
                    _save_clips(
                        st.session_state.job_path, st.session_state.clips,
                    )
                    st.success(f"Auto-detect complete! {len(rallies)} rallies added.")
                else:
                    st.info("Auto-detect complete but no rallies detected.")
            st.rerun()
        except Exception as e:
            st.error(f"Auto-detect failed: {e}")

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
                st.session_state.clips = [
                    c for c in st.session_state.clips if c["id"] != clip_id
                ]
                _save_clips(st.session_state.job_path, st.session_state.clips)
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
                for c in st.session_state.clips:
                    if c["id"] == clip_id:
                        c["is_highlight"] = is_highlight
                        break
                _save_clips(st.session_state.job_path, st.session_state.clips)
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
        f"audio({bd['conf_audio']:.2f} x 0.3) + "
        f"video({bd['conf_video_norm']:.2f} x 0.5) + "
        f"rhythm({bd['rhythm_score']:.2f} x 0.2)"
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
    """Interactive ROI editor using streamlit-drawable-canvas."""
    from streamlit_drawable_canvas import st_canvas
    from PIL import Image

    st.markdown("**Manual ROI Editor** — click 4 points for table, draw rect for scoreboard")

    with st.expander("Edit Table ROI (4-point click)", expanded=False):
        img = Image.open(str(frame0_path))
        img_w, img_h = img.size

        # Scale to fit UI
        canvas_w = min(800, img_w)
        scale = canvas_w / img_w
        canvas_h = int(img_h * scale)

        canvas_result = st_canvas(
            fill_color="rgba(255, 0, 0, 0.3)",
            stroke_width=3,
            stroke_color="#00FF00",
            background_image=img,
            drawing_mode="point",
            point_display_radius=6,
            width=canvas_w,
            height=canvas_h,
            key="table_roi_canvas",
        )

        # Show existing proposal overlay
        roi_path = art / "table_roi.json"
        if roi_path.exists():
            with open(roi_path, "r") as f:
                current_roi = json.load(f)
            pts = current_roi.get("table_polygon", [])
            if pts:
                st.caption(
                    f"Current: {pts} "
                    f"(source={current_roi.get('source', '?')}, "
                    f"conf={current_roi.get('confidence', '?')})"
                )

        col_u1, col_u2, col_u3 = st.columns(3)
        with col_u1:
            if st.button("Use Auto Proposal", key="use_auto_table"):
                # Re-run auto-detect and save
                try:
                    _run_step("setup")
                    st.success("Auto proposal applied!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Auto-detect failed: {e}")
        with col_u2:
            if st.button("Reset Canvas", key="reset_table_canvas"):
                st.rerun()
        with col_u3:
            if st.button("Save Manual Table ROI", key="save_manual_table"):
                if (canvas_result is not None
                        and canvas_result.json_data is not None):
                    objects = canvas_result.json_data.get("objects", [])
                    points = [
                        [int(obj["left"] / scale), int(obj["top"] / scale)]
                        for obj in objects
                        if obj.get("type") == "circle"
                    ]
                    if len(points) == 4:
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
                        # Mark setup as complete
                        _mark_setup_complete(art)
                        st.success("Manual table ROI saved!")
                        st.rerun()
                    else:
                        st.warning(f"Need exactly 4 points, got {len(points)}.")
                else:
                    st.warning("No points drawn on canvas.")

    with st.expander("Edit Scoreboard ROI (rectangle)", expanded=False):
        img = Image.open(str(frame0_path))
        img_w, img_h = img.size
        canvas_w = min(800, img_w)
        scale = canvas_w / img_w
        canvas_h = int(img_h * scale)

        sb_canvas = st_canvas(
            fill_color="rgba(0, 0, 255, 0.2)",
            stroke_width=2,
            stroke_color="#0000FF",
            background_image=img,
            drawing_mode="rect",
            width=canvas_w,
            height=canvas_h,
            key="scoreboard_roi_canvas",
        )

        sb_path = art / "scoreboard_roi.json"
        if sb_path.exists():
            with open(sb_path, "r") as f:
                sb_info = json.load(f)
            if sb_info.get("enabled"):
                r = sb_info["rect"]
                st.caption(f"Current: x={r['x']}, y={r['y']}, w={r['w']}, h={r['h']}")
            else:
                st.caption("Scoreboard ROI: disabled")

        if st.button("Save Scoreboard ROI", key="save_scoreboard"):
            if (sb_canvas is not None
                    and sb_canvas.json_data is not None):
                objects = sb_canvas.json_data.get("objects", [])
                rects = [
                    obj for obj in objects if obj.get("type") == "rect"
                ]
                if rects:
                    r = rects[-1]  # Use the last drawn rect
                    sb_data = {
                        "enabled": True,
                        "rect": {
                            "x": int(r["left"] / scale),
                            "y": int(r["top"] / scale),
                            "w": int(r["width"] / scale),
                            "h": int(r["height"] / scale),
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
                    st.warning("No rectangle drawn.")
            else:
                st.warning("No rectangle drawn.")


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
        out_path = clips_dir / f"{safe_label}.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(clip_start), "-i", input_video, "-t", str(duration),
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-level", "4.1",
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


main()
