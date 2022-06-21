import os
import shutil
from pathlib import Path
from typing import Optional

import yaml
from diffusers import DiffusionPipeline
from huggingface_hub import HfFolder, Repository, whoami

from .utils import logging


logger = logging.get_logger(__name__)


AUTOGENERATED_TRAINER_COMMENT = """
<!-- This model card has been generated automatically according to the information the Trainer had access to. You
should probably proofread and complete it, then remove this comment. -->
"""


def get_full_repo_name(model_id: str, organization: Optional[str] = None, token: Optional[str] = None):
    if token is None:
        token = HfFolder.get_token()
    if organization is None:
        username = whoami(token)["name"]
        return f"{username}/{model_id}"
    else:
        return f"{organization}/{model_id}"


def init_git_repo(args, at_init: bool = False):
    """
    Initializes a git repo in `args.hub_model_id`.
    Args:
        at_init (`bool`, *optional*, defaults to `False`):
            Whether this function is called before any training or not. If `self.args.overwrite_output_dir` is
            `True` and `at_init` is `True`, the path to the repo (which is `self.args.output_dir`) might be wiped
            out.
    """
    if args.local_rank not in [-1, 0]:
        return
    use_auth_token = True if args.hub_token is None else args.hub_token
    if args.hub_model_id is None:
        repo_name = Path(args.output_dir).absolute().name
    else:
        repo_name = args.hub_model_id
    if "/" not in repo_name:
        repo_name = get_full_repo_name(repo_name, token=args.hub_token)

    try:
        repo = Repository(
            args.output_dir,
            clone_from=repo_name,
            use_auth_token=use_auth_token,
            private=args.hub_private_repo,
        )
    except EnvironmentError:
        if args.overwrite_output_dir and at_init:
            # Try again after wiping output_dir
            shutil.rmtree(args.output_dir)
            repo = Repository(
                args.output_dir,
                clone_from=repo_name,
                use_auth_token=use_auth_token,
            )
        else:
            raise

    repo.git_pull()

    # By default, ignore the checkpoint folders
    if not os.path.exists(os.path.join(args.output_dir, ".gitignore")) and args.hub_strategy != "all_checkpoints":
        with open(os.path.join(args.output_dir, ".gitignore"), "w", encoding="utf-8") as writer:
            writer.writelines(["checkpoint-*/"])

    return repo


def push_to_hub(
    args,
    pipeline: DiffusionPipeline,
    repo: Repository,
    commit_message: Optional[str] = "End of training",
    blocking: bool = True,
    **kwargs,
) -> str:
    """
    Upload *self.model* and *self.tokenizer* to the 🤗 model hub on the repo *self.args.hub_model_id*.
    Parameters:
        commit_message (`str`, *optional*, defaults to `"End of training"`):
            Message to commit while pushing.
        blocking (`bool`, *optional*, defaults to `True`):
            Whether the function should return only when the `git push` has finished.
        kwargs:
            Additional keyword arguments passed along to [`create_model_card`].
    Returns:
        The url of the commit of your model in the given repository if `blocking=False`, a tuple with the url of
        the commit and an object to track the progress of the commit if `blocking=True`
    """

    if args.hub_model_id is None:
        model_name = Path(args.output_dir).name
    else:
        model_name = args.hub_model_id.split("/")[-1]

    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)
    logger.info(f"Saving pipeline checkpoint to {output_dir}")
    pipeline.save_pretrained(output_dir)

    # Only push from one node.
    if args.local_rank not in [-1, 0]:
        return

    # Cancel any async push in progress if blocking=True. The commits will all be pushed together.
    if (
        blocking
        and len(repo.command_queue) > 0
        and repo.command_queue[-1] is not None
        and not repo.command_queue[-1].is_done
    ):
        repo.command_queue[-1]._process.kill()

    git_head_commit_url = repo.push_to_hub(commit_message=commit_message, blocking=blocking, auto_lfs_prune=True)
    # push separately the model card to be independent from the rest of the model
    create_model_card(args, model_name=model_name)
    try:
        repo.push_to_hub(commit_message="update model card README.md", blocking=blocking, auto_lfs_prune=True)
    except EnvironmentError as exc:
        logger.error(f"Error pushing update to the model card. Please read logs and retry.\n${exc}")

    return git_head_commit_url


def create_model_card(args, model_name):
    if args.local_rank not in [-1, 0]:
        return

    # TODO: replace this placeholder model card generation
    model_card = ""

    metadata = {"license": "apache-2.0", "tags": ["pytorch", "diffusers"]}
    metadata = yaml.dump(metadata, sort_keys=False)
    if len(metadata) > 0:
        model_card = f"---\n{metadata}---\n"

    model_card += AUTOGENERATED_TRAINER_COMMENT

    model_card += f"\n# {model_name}\n\n"

    with open(os.path.join(args.output_dir, "README.md"), "w") as f:
        f.write(model_card)
