import jsonschema
import docker
import io
import json
import logging
import os
import shutil
import sys
from colorama import Fore, Style
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from urllib.request import urlretrieve
from hashlib import sha1


# download_cache_path = os.path.join(build_path, "download_cache")
# artifacts_path = os.path.join(build_path, "artifacts")
# pages_path = os.path.join(root_path, "pages")
# verbose = True

requirements = [
    "numpy",
    "pandas",
    "xlrd",
    "openpyxl",
    "markdownify",
    "sentence_transformers",
]
dockerfile = (
    """FROM python:3.11-slim
ARG UID=1000
ARG GID=1000
RUN addgroup appgroup --gid "$GID" 
RUN adduser appuser --uid "$UID" --gid "$GID" --disabled-password --gecos ""
USER appuser
RUN pip install --no-cache-dir --upgrade pip --no-warn-script-location
RUN pip install --no-cache-dir """
    + " ".join(requirements)
    + """ --no-warn-script-location
WORKDIR /app
ENTRYPOINT ["python", "/app/recipe/generate.py"]"""
)


def build_docker_python_sandbox_image():
    logger = logging.getLogger("complott")
    logger.info("Building Docker image...")
    client = docker.from_env()
    try:
        logs = client.api.build(
            fileobj=io.BytesIO(dockerfile.encode("utf-8")),
            tag="recipe-sandbox",
            buildargs={"UID": str(os.getuid()), "GID": str(os.getgid())},
            decode=True,
        )
        for entry in logs:
            if "stream" in entry:
                line = entry["stream"]
                if line[-1] == "\n":
                    line = line[:-1]
                if len(line) == 0:
                    continue
                logger.debug(line)
    except docker.errors.BuildError as e:
        if e.build_log:
            for log_entry in e.build_log:
                if "stream" in log_entry:
                    logger.debug(log_entry["stream"])
                elif "errorDetail" in log_entry:
                    logger.error(log_entry["errorDetail"]["message"])
        sys.exit(os.EX_CONFIG)


versions_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "patternProperties": {
        "^.*$": {
            "type": "object",
            "properties": {
                "folder": {
                    "type": "string",
                    "pattern": '^(?![ .])[^<>:"/\\|?*\r\n]+(?<![ .])$',
                },
                "build_folder_alias": {
                    "type": "string",
                    "pattern": '^(?![ .])[^<>:"/\\|?*\r\n]+(?<![ .])$',
                },
            },
            "required": ["folder"],
            "additionalProperties": False,
        }
    },
    "additionalProperties": False,
}
recipe_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "recipe_type": {
            "type": "string",
            "enum": ["python"],
        },
        "dependencies": {
            "type": "array",
            "items": {
                "type": "object",
                "oneOf": [
                    {
                        "properties": {
                            "type": {"type": "string", "const": "fetch"},
                            "url": {
                                "type": "string",
                                "pattern": "^https?:\/\/(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}(?:[-a-zA-Z0-9()@:%_\+.~#?&\/=]*)$",
                            },
                            "file_name": {
                                "type": "string",
                                "pattern": '^(?![ .])[^<>:"/\\|?*\r\n]+(?<![ .])$',
                            },
                        },
                        "required": ["type", "url"],
                        "additionalProperties": False,
                    },
                    {
                        "properties": {
                            "type": {"type": "string", "const": "build"},
                            "recipe_name": {
                                "type": "string",
                                "pattern": '^(?![ .])[^<>:"/\\|?*\r\n]+(?<![ .])$',
                            },
                            "version": {"type": "string"},
                        },
                        "required": ["type", "recipe_name"],
                        "additionalProperties": False,
                    },
                ],
            },
        },
    },
    "required": ["recipe_type", "dependencies"],
    "additionalProperties": False,
}


def read_recipes(recipes_path):
    logger = logging.getLogger("complott")
    logger.info("Reading recipes...")
    recipes = {}
    for item in os.listdir(recipes_path):
        if not os.path.isdir(os.path.join(recipes_path, item)):
            continue
        recipe_name = item
        recipe_path = os.path.join(recipes_path, recipe_name)

        versions_json_path = os.path.join(recipe_path, "versions.json")
        if not os.path.exists(versions_json_path):
            logger.warning(
                f"Skipped recipe '{recipe_name}', 'versions.json' not found."
            )
            continue
        with open(versions_json_path) as version_file:
            versions = json.load(version_file)
            try:
                jsonschema.validate(versions, versions_schema)
            except jsonschema.ValidationError as e:
                logger.warning(
                    f"Skipped recipe '{recipe_name}', 'versions.json' has invalid scheme:\n ---> "
                    + e.schema.get("error_msg", e.message)
                )
                continue

        recipes[recipe_name] = dict()
        for version_tag, version in versions.items():
            recipe_version_path = os.path.join(recipe_path, version["folder"])
            if "build_folder_alias" not in version:
                version["build_folder_alias"] = version["folder"]

            recipe_json_path = os.path.join(recipe_version_path, "recipe.json")
            if not os.path.exists(recipe_json_path):
                logger.warning(
                    f"Skipped recipe '{recipe_name}/{version_tag}', 'recipe.json' not found."
                )
                continue
            with open(recipe_json_path) as recipe_file:
                recipe = json.load(recipe_file)
                try:
                    jsonschema.validate(recipe, recipe_schema)
                except jsonschema.ValidationError as e:
                    logger.warning(
                        f"Skipped recipe '{recipe_name}/{version_tag}', 'recipe.json' has invalid scheme:\n ---> "
                        + e.schema.get("error_msg", e.message)
                    )
                    continue

                recipes[recipe_name][version_tag] = recipe | version


# def read_recipes(recipes_path):
#     logger = logging.getLogger("complott")
#     logger.info("Reading recipes...")
#     recipes = {}
#     for item in os.listdir(recipes_path):
#         if not os.path.isdir(os.path.join(recipes_path, item)):
#             print(
#                 Fore.YELLOW
#                 + f"Warning: File '{item}' should not be in the 'recipes' folder."
#                 + Style.RESET_ALL
#             )
#             continue
#         recipe_name = item
#         recipe_path = os.path.join(recipes_path, recipe_name)

#         recipe_path = os.path.join(recipe_path, "recipe.json")
#         if not os.path.exists(recipe_path):
#             print(
#                 Fore.YELLOW
#                 + f"Warning: skipped recipe '{recipe_name}': 'recipe.json' not found."
#                 + Style.RESET_ALL
#             )
#             continue
#         with open(recipe_path) as f:
#             d = json.load(f)
#             try:
#                 jsonschema.validate(d, recipe_schema)
#             except jsonschema.ValidationError as e:
#                 print(
#                     Fore.YELLOW
#                     + f"Warning: skipped recipe '{recipe_name}' because 'recipe.json' has invalid scheme:\n"
#                     + e.schema.get("error_msg", e.message)
#                     + Style.RESET_ALL
#                 )
#                 continue
#             if "required_resources" not in d:
#                 d["required_resources"] = {}
#             if "generated" not in d["required_resources"]:
#                 d["required_resources"]["generated"] = []
#             if "downloaded" not in d["required_resources"]:
#                 d["required_resources"]["downloaded"] = {}
#             if "generate_resources" not in d:
#                 d["generate_resources"] = False
#             recipes[recipe_name] = d


# print(Fore.GREEN + "Verifying integrity......" + Style.RESET_ALL)


def normalize_url(url):
    parsed = urlparse(url)
    netloc = parsed.hostname.lower() if parsed.hostname else ""
    if parsed.port and parsed.port not in (80, 443):
        netloc += f":{parsed.port}"
    path = parsed.path.rstrip("/")
    query = urlencode(sorted(parse_qsl(parsed.query)))
    return urlunparse((parsed.scheme.lower(), netloc, path, parsed.params, query, ""))


def hash_string(s):
    return str(int(sha1(s.encode("utf-8")).hexdigest(), 16) % 10**16)


# invalid_recipe_names = []
# resources_to_generate_for_recipes = {}
# resources_to_download_for_recipes = {}
# for recipe_name, recipe in recipes.items():
#     for required_recipe_name in recipe["required_resources"]["generated"]:
#         if required_recipe_name not in recipes:
#             print(
#                 Fore.YELLOW
#                 + f"Warning: skipped recipe '{recipe_name}' because dependency '{required_recipe_name}' is unknown."
#                 + Style.RESET_ALL
#             )
#             invalid_recipe_names.append(recipe_name)
#             continue
#         if not recipes[required_recipe_name]["generate_resources"]:
#             print(
#                 Fore.YELLOW
#                 + f"Warning: skipped recipe '{recipe_name}' because dependency '{required_recipe_name}' does not generate resources."
#                 + Style.RESET_ALL
#             )
#             invalid_recipe_names.append(recipe_name)
#             continue
#         if required_recipe_name not in resources_to_generate_for_recipes:
#             resources_to_generate_for_recipes[required_recipe_name] = []
#         resources_to_generate_for_recipes[required_recipe_name].append(recipe_name)

#     for resource_alias, resource_url in recipe["required_resources"][
#         "downloaded"
#     ].items():
#         normalized_resource_url = normalize_url(resource_url)
#         recipe["required_resources"]["downloaded"][
#             resource_alias
#         ] = normalized_resource_url
#         if normalized_resource_url not in resources_to_download_for_recipes:
#             resources_to_download_for_recipes[normalized_resource_url] = []
#         resources_to_download_for_recipes[normalized_resource_url].append(recipe_name)

# for invalid_recipe_name in invalid_recipe_names:
#     del recipes[recipe_name]

# unfulfilled_recipes_requirements = {
#     recipe_name: {
#         "downloaded": list(recipe["required_resources"]["downloaded"].values()),
#         "generated": recipe["required_resources"]["generated"].copy(),
#     }
#     for recipe_name, recipe in recipes.items()
# }


# def notify_downloaded_resource(resource_url):
#     for requiring_recipe in resources_to_download_for_recipes[resource_url]:
#         unfulfilled_recipes_requirements[requiring_recipe]["downloaded"].remove(
#             resource_url
#         )


# print(Fore.GREEN + "Downloading resources..." + Style.RESET_ALL)
# if not os.path.exists(download_cache_path):
#     os.makedirs(download_cache_path)
# resources_index_path = os.path.join(download_cache_path, "index.json")
# if os.path.exists(resources_index_path):
#     with open(resources_index_path, "r") as f:
#         resources_index = json.load(f)
# else:
#     resources_index = {}

# for resource_url, requiring_recipes in resources_to_download_for_recipes.items():
#     if resource_url not in resources_index:
#         resource_file_name = hash_string(resource_url)
#         resource_path = os.path.join(download_cache_path, resource_file_name)
#         try:
#             urlretrieve(resource_url, resource_path)
#         except Exception as e:
#             print(
#                 Fore.YELLOW
#                 + f"Warning: failed to download resource '{resource_url}' ({e})"
#                 + Style.RESET_ALL
#             )
#             if os.path.exists(resource_path):
#                 os.remove(resource_path)
#             continue
#         resources_index[resource_url] = resource_file_name
#         if verbose:
#             print("Donwload: ", end="")
#     else:
#         if verbose:
#             print("In cache: ", end="")
#     url_length = len(resource_url)
#     if verbose:
#         if url_length <= 70:
#             print(resource_url)
#         else:
#             print(f"{resource_url[:20]}...{resource_url[-46:]}")
#     notify_downloaded_resource(resource_url)

# with open(resources_index_path, "w") as f:
#     json.dump(resources_index, f, indent=4)

# print(Fore.GREEN + "Running recipes.........." + Style.RESET_ALL)
# import queue

# recipes_to_run = queue.Queue()


# def can_recipe_be_run(recipe_name):
#     unfulfilled_downloads = unfulfilled_recipes_requirements[recipe_name]["downloaded"]
#     unfulfilled_generations = unfulfilled_recipes_requirements[recipe_name]["generated"]
#     return len(unfulfilled_downloads) == 0 and len(unfulfilled_generations) == 0


# def notify_generated_resource(resource_name):
#     if resource_name not in resources_to_generate_for_recipes:
#         return
#     for requiring_recipe_name in resources_to_generate_for_recipes[resource_name]:
#         unfulfilled_recipes_requirements[requiring_recipe_name]["generated"].remove(
#             resource_name
#         )
#         if can_recipe_be_run(requiring_recipe_name):
#             recipes_to_run.put(requiring_recipe_name)


# for recipe_name in recipes.keys():
#     if can_recipe_be_run(recipe_name):
#         recipes_to_run.put(recipe_name)


# while not recipes_to_run.empty():
#     recipe_name = recipes_to_run.get()
#     recipe = recipes[recipe_name]

#     volumes = {}
#     volumes[os.path.join(recipes_path, recipe_name)] = {
#         "bind": "/app/recipe",
#         "mode": "ro",
#     }
#     for resource_alias, resource_url in recipe["required_resources"][
#         "downloaded"
#     ].items():
#         volumes[os.path.join(download_cache_path, resources_index[resource_url])] = {
#             "bind": f"/app/resources/downloaded/{resource_alias}",
#             "mode": "ro",
#         }
#     for required_resource_name in recipe["required_resources"]["generated"]:
#         volumes[os.path.join(generated_resources_path, required_resource_name)] = {
#             "bind": f"/app/resources/generated/{required_resource_name}",
#             "mode": "ro",
#         }
#     if recipe["generate_resources"]:
#         path = os.path.join(generated_resources_path, recipe_name)
#         if os.path.exists(path):
#             shutil.rmtree(path)
#         os.makedirs(path)
#         volumes[path] = {
#             "bind": f"/app/resources/generated/{recipe_name}",
#             "mode": "rw",
#         }
#     for page_folder in recipe["pages"].values():
#         path = os.path.join(pages_path, recipe_name, page_folder)
#         if not os.path.exists(path):
#             os.makedirs(path)
#         volumes[path] = {
#             "bind": f"/app/pages/{recipe_name}/{page_folder}",
#             "mode": "rw",
#         }

#     if verbose:
#         print(f"Running recipe '{recipe_name}'")

#     try:
#         container_logs = client.containers.run(
#             "recipe-sandbox",
#             remove=True,
#             volumes=volumes,
#             network_disabled=True,
#             mem_limit="1000m",
#         )
#         if verbose:
#             print(container_logs.decode("utf-8"), end="")
#     except docker.errors.ContainerError as e:
#         match e.exit_status:
#             case 1:
#                 print(Fore.RED + e.stderr.decode("utf-8") + Style.RESET_ALL)
#                 continue
#             case 137:
#                 print(Fore.RED + "Container exceeded memory limit" + Style.RESET_ALL)
#                 continue

#     if recipe["generate_resources"]:
#         notify_generated_resource(recipe_name)

# unbuilt_recipes_names = [
#     recipe_name for recipe_name in recipes.keys() if not can_recipe_be_run(recipe_name)
# ]
# if len(unbuilt_recipes_names) > 0:
#     print(
#         Fore.YELLOW
#         + f"Warning: The following recipes were not built because of missing requirements:"
#     )
#     for recipe_name in unbuilt_recipes_names:
#         print(
#             f"\t-{recipe_name} ({unfulfilled_recipes_requirements[recipe_name]['generated']},{unfulfilled_recipes_requirements[recipe_name]['downloaded']})"
#         )
#     print(Style.RESET_ALL)
