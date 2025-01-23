import click
import logging
from colorama import Fore, Style

from complott.complott import build_docker_python_sandbox_image, read_recipes


class CustomFormatter(logging.Formatter):
    level_format = "[%(levelname)s] %(message)s"
    format = "%(message)s"
    FORMATS = {
        logging.DEBUG: Fore.LIGHTWHITE_EX + format + Style.RESET_ALL,
        logging.INFO: Fore.GREEN + level_format + Style.RESET_ALL,
        logging.WARNING: Fore.YELLOW + level_format + Style.RESET_ALL,
        logging.ERROR: Fore.LIGHTRED_EX + level_format + Style.RESET_ALL,
        logging.CRITICAL: Fore.RED + level_format + Style.RESET_ALL,
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)


logger = logging.getLogger("complott")
logger.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
ch.setFormatter(CustomFormatter())
logger.addHandler(ch)


@click.group()
def cli():
    """ComPlotT (Community-driven Plotting Tool)"""
    pass


@cli.command()
@click.argument("recipes_folder", type=click.Path())
@click.option(
    "--build-folder",
    "-of",
    default="./build",
    help="The folder containing all recipes.",
    type=click.Path(),
)
def build(recipes_folder, build_folder):
    """Build all recipes"""

    build_docker_python_sandbox_image()
    recipes = read_recipes(recipes_folder)


if __name__ == "__main__":
    cli()
