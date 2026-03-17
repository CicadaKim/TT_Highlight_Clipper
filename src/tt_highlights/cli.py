"""CLI entry point for tt_highlights pipeline."""

import sys
import logging

import click

from .job import create_job, load_job, job_dir
from .config import load_config
from .steps import STEP_ORDER, get_step_function
from .steps.setup import is_setup_complete

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("tt_highlights")


@click.group()
def cli():
    """TT Highlight Clipper – automated table tennis highlight extraction."""
    pass


@cli.command()
@click.option("--input", "input_video", required=True, help="Path to input video file.")
@click.option("--out", "out_dir", default="out", help="Base output directory.")
def init(input_video: str, out_dir: str):
    """Initialize a new job: create job.json and config.yaml."""
    try:
        job_path = create_job(input_video, out_dir)
        click.echo(f"Job created: {job_path}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("step_name")
@click.option("--job", "job_path", required=True, help="Path to job.json.")
@click.option("--auto-accept-setup", is_flag=True, default=False,
              help="Accept low-confidence setup results without review.")
def step(step_name: str, job_path: str, auto_accept_setup: bool):
    """Run a single pipeline step."""
    try:
        job = load_job(job_path)
        config = load_config(str(job_dir(job_path) / "config.yaml"))
        step_fn = get_step_function(step_name)
        logger.info(f"Running step: {step_name}")
        if step_name == "setup":
            step_fn(job, config, job_path, auto_accept=auto_accept_setup)
        else:
            step_fn(job, config, job_path)
        logger.info(f"Step '{step_name}' completed successfully.")
    except Exception as e:
        logger.error(f"Step '{step_name}' failed: {e}", exc_info=True)
        sys.exit(1)


@cli.command()
@click.option("--job", "job_path", required=True, help="Path to job.json.")
@click.option("--skip-on-fail", is_flag=True, default=False,
              help="Continue to next step on failure instead of stopping.")
@click.option("--auto-accept-setup", is_flag=True, default=False,
              help="Accept auto-detected setup (table/scoreboard ROI) without review.")
def run_all(job_path: str, skip_on_fail: bool, auto_accept_setup: bool):
    """Run all pipeline steps in order."""
    job = load_job(job_path)
    config = load_config(str(job_dir(job_path) / "config.yaml"))

    # Vision steps that require setup to be complete
    vision_steps = {
        "audio_events", "video_activity", "rally_segment",
        "scoreboard_ocr", "ball_tracking", "features",
        "scoring", "selection", "export",
    }

    for step_name in STEP_ORDER:
        # Gate: vision steps require completed setup
        if step_name in vision_steps and not is_setup_complete(job_path):
            if auto_accept_setup:
                logger.info("Setup not complete but --auto-accept-setup is set. "
                            "Running setup with auto-accept...")
                try:
                    setup_fn = get_step_function("setup")
                    setup_fn(job, config, job_path, auto_accept=True)
                    logger.info("Setup auto-completed.")
                except Exception as e:
                    logger.error(f"Auto-setup failed: {e}", exc_info=True)
                    if not skip_on_fail:
                        click.echo(
                            "Setup failed. Cannot proceed with vision steps.",
                            err=True,
                        )
                        sys.exit(1)
            else:
                click.echo(
                    f"Setup not completed. Cannot run '{step_name}'. "
                    "Run setup first or use --auto-accept-setup.",
                    err=True,
                )
                sys.exit(1)

        try:
            step_fn = get_step_function(step_name)
            logger.info(f"=== Running step: {step_name} ===")
            if step_name == "setup" and auto_accept_setup:
                step_fn(job, config, job_path, auto_accept=True)
            else:
                step_fn(job, config, job_path)
            logger.info(f"Step '{step_name}' completed.")
        except Exception as e:
            logger.error(f"Step '{step_name}' failed: {e}", exc_info=True)
            if skip_on_fail:
                logger.warning(f"Skipping failed step '{step_name}' (--skip-on-fail)")
                continue
            else:
                click.echo(f"Pipeline stopped at step '{step_name}'. Use --skip-on-fail to continue.", err=True)
                sys.exit(1)

    click.echo("All steps completed.")


if __name__ == "__main__":
    cli()
