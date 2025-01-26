import abc
import docker
import filecmp
import graphlib
import hashlib
import io
import json
import jsonschema
import logging
import os
import queue
import shutil
import sys
import urllib.parse
import urllib.request

Dependencys = [
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
    + " ".join(Dependencys)
    + """ --no-warn-script-location
WORKDIR /app
ENTRYPOINT ["python", "recipe/generate.py"]"""
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
                "artifact_folder": {
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


class Artifact(abc.ABC):
    @abc.abstractmethod
    def build(self, recipes_folder, build_folder, artifacts, override=False):
        pass

    @abc.abstractmethod
    def get_build_path(self, build_folder):
        pass

    @abc.abstractmethod
    def id(self):
        pass


class Recipe(Artifact):
    def __init__(self, recipe_name, recipe_version, version_json, dependencies):
        self.name = recipe_name
        self.version = recipe_version
        self.version_source_folder = version_json["folder"]
        if "folder_alias" in version_json:
            self.version_build_folder = version_json["folder_alias"]
        else:
            self.version_build_folder = self.version_source_folder
        self.dependencies = dependencies

    def get_source_path(self, recipes_folder):
        return os.path.join(recipes_folder, self.name, self.version_source_folder)

    def get_build_path(self, build_folder):
        return os.path.join(
            build_folder, "recipes", self.name, self.version_build_folder
        )

    def id(self):
        return f"{Recipe.__name__}:{self.name}/{self.version}"


def left_files_changed(dcmp):
    if len(dcmp.left_only) > 0:
        return True
    if len(dcmp.diff_files) > 0:
        return True
    for sub_dcmp in dcmp.subdirs.values():
        if left_files_changed(sub_dcmp):
            return True
    return False


class PythonRecipe(Recipe):
    def __init__(self, recipe_name, recipe_version, version_json, dependencies):
        super().__init__(recipe_name, recipe_version, version_json, dependencies)

    def build(self, recipes_folder, build_folder, artifacts, override=False):
        super().build(recipes_folder, build_folder, artifacts, override)
        logger = logging.getLogger("complott")

        recipe_path = self.get_source_path(recipes_folder)
        build_path = self.get_build_path(build_folder)

        if os.path.exists(build_path):
            if (
                not left_files_changed(filecmp.dircmp(recipe_path, build_path))
                and not override
            ):
                logger.debug(f"({self.name}): Skipped, recipe did not changed.")
                return
            shutil.rmtree(build_path)

        shutil.copytree(recipe_path, build_path)

        volumes = {}
        volumes[build_path] = {
            "bind": "/app/recipe",
            "mode": "ro",
        }
        data_path = os.path.join(build_path, "data")
        if not os.path.exists(os.path.join(build_path, "data")):
            os.makedirs(data_path)
        volumes[data_path] = {
            "bind": "/app/data",
            "mode": "rw",
        }
        for dependency in self.dependencies:
            volumes[
                artifacts[dependency.artifact_id()].get_build_path(build_folder)
            ] = {
                "bind": f"/app/dependencies/{dependency.get_mounting_path()}",
                "mode": "ro",
            }

        logger.info(f"({self.name}): Running recipe...")
        try:
            client = docker.from_env()
            container_logs = client.containers.run(
                "recipe-sandbox",
                remove=True,
                volumes=volumes,
                network_disabled=True,
                mem_limit="1000m",
            )
            logger.debug(f"({self.name}): {container_logs.decode('utf-8')}")
        except docker.errors.ContainerError as e:
            match e.exit_status:
                case 1:
                    logger.error(f"({self.name}): {e.stderr.decode('utf-8')}")
                    raise e
                case 137:
                    logger.error(f"({self.name}): Container exceeded memory limit.")
                    raise e


recipe_types = {"python": PythonRecipe}


def normalize_url(url):
    parsed = urllib.parse.urlparse(url)
    netloc = parsed.hostname.lower() if parsed.hostname else ""
    if parsed.port and parsed.port not in (80, 443):
        netloc += f":{parsed.port}"
    path = parsed.path.rstrip("/")
    query = urllib.parse.urlencode(sorted(urllib.parse.parse_qsl(parsed.query)))
    return urllib.parse.urlunparse(
        (parsed.scheme.lower(), netloc, path, parsed.params, query, "")
    )


class Fetch(Artifact):
    def __init__(self, dependency_json):
        super().__init__()
        self.url = normalize_url(dependency_json["url"])

    def get_build_path(self, build_folder):
        cache_file_name = hashlib.sha1(self.url.encode("utf-8")).hexdigest()[:24]
        return os.path.join(build_folder, "fetch_cache", cache_file_name)

    def id(self):
        return f"{Fetch.__name__}:{self.url}"

    def _compact_url(self, max_length):
        url_length = len(self.url)
        if url_length <= max_length:
            return self.url
        else:
            return f"{self.url[:20]}...{self.url[-46:]}"

    def build(self, recipes_folder, build_folder, artifacts, override=False):
        logger = logging.getLogger("complott")
        cache_file_path = self.get_build_path(build_folder)
        if os.path.exists(cache_file_path) and not override:
            logger.debug(f"In cache: {self._compact_url(70)}")
            return True

        try:
            fetch_folder = os.path.join(build_folder, "fetch_cache")
            if not os.path.exists(fetch_folder):
                os.mkdir(fetch_folder)
            urllib.request.urlretrieve(self.url, cache_file_path)
        except Exception as e:
            logger.error(f"Failed to download resource '{self.url}'\n ---> {e}")
            if os.path.exists(cache_file_path):
                os.remove(cache_file_path)
            raise e

        logger.debug(f"Fetched:  {self._compact_url(70)}")
        return True


class Dependency(abc.ABC):
    @abc.abstractmethod
    def get_mounting_path(self):
        pass

    @abc.abstractmethod
    def artifact_id(self):
        pass


class FetchDependency(Dependency):
    def __init__(self, artifact, dependency_json):
        super().__init__()
        self._artifact = artifact
        if "file_name" in dependency_json:
            self.file_name = dependency_json["file_name"]
        else:
            self.file_name = artifact.url[artifact.url.rfind("/") + 1 :]

    def get_mounting_path(self):
        return f"fetch/{self.file_name}"

    def artifact_id(self):
        return self._artifact.id()


def register_fetch_dependency(artifacts, dependency_json):
    artifact = Fetch(dependency_json)
    artifact_id = artifact.id()
    if artifact_id not in artifacts:
        artifacts[artifact_id] = artifact
    return FetchDependency(artifact, dependency_json)


class RecipeDependency(Dependency):
    def __init__(self, dependency_json):
        super().__init__()
        self.recipe_name = dependency_json["recipe_name"]
        self.version = dependency_json["version"]

    def get_mounting_path(self):
        return f"recipes/{self.recipe_name}/{self.version}/data"

    def artifact_id(self):
        return f"{Recipe.__name__}:{self.recipe_name}/{self.version}"


def register_recipe_dependency(artifacts, dependency_json):
    return RecipeDependency(dependency_json)


dependency_types = {
    "fetch": {
        "schema": {
            "properties": {
                "type": {},
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
        "register_function": register_fetch_dependency,
    },
    "build": {
        "schema": {
            "properties": {
                "type": {},
                "recipe_name": {
                    "type": "string",
                    "pattern": '^(?![ .])[^<>:"/\\|?*\r\n]+(?<![ .])$',
                },
                "version": {"type": "string"},
            },
            "required": ["type", "recipe_name", "version"],
            "additionalProperties": False,
        },
        "register_function": register_recipe_dependency,
    },
}
recipe_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "recipe_type": {
            "type": "string",
            "enum": list(recipe_types.keys()),
        },
        "dependencies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"type": {"enum": list(dependency_types.keys())}},
                "allOf": [
                    {
                        "if": {"properties": {"type": {"const": type_name}}},
                        "then": type["schema"],
                    }
                    for type_name, type in dependency_types.items()
                ],
            },
        },
    },
    "required": ["recipe_type", "dependencies"],
    "additionalProperties": False,
}


def read_recipes(recipes_folder):
    logger = logging.getLogger("complott")
    logger.info("Reading recipes...")
    artifacts = dict()
    for item in os.listdir(recipes_folder):
        if not os.path.isdir(os.path.join(recipes_folder, item)):
            continue
        recipe_name = item
        recipe_path = os.path.join(recipes_folder, recipe_name)

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

        for version_name, version_json in versions.items():
            recipe_version_path = os.path.join(recipe_path, version_json["folder"])
            recipe_json_path = os.path.join(recipe_version_path, "recipe.json")
            if not os.path.exists(recipe_json_path):
                logger.warning(
                    f"Skipped recipe '{recipe_name}/{version_name}', 'recipe.json' not found."
                )
                continue
            with open(recipe_json_path) as recipe_file:
                recipe_json = json.load(recipe_file)
                try:
                    jsonschema.validate(recipe_json, recipe_schema)
                except jsonschema.ValidationError as e:
                    logger.warning(
                        f"Skipped recipe '{recipe_name}/{version_name}', 'recipe.json' has invalid scheme:\n ---> "
                        + e.schema.get("error_msg", e.message)
                    )
                    continue

                recipe_dependencies = []
                for dependency_json in recipe_json["dependencies"]:
                    dependency_type = dependency_json["type"]
                    if dependency_type not in dependency_types:
                        logger.critical(
                            "Dependency type '{}' is unknwon but passed JSON validation.".format(
                                dependency_type
                            )
                        )
                        sys.exit(os.EX_CONFIG)

                    recipe_dependencies.append(
                        dependency_types[dependency_type]["register_function"](
                            artifacts, dependency_json
                        )
                    )
                recipe = recipe_types[recipe_json["recipe_type"]](
                    recipe_name, version_name, version_json, recipe_dependencies
                )
                artifacts[recipe.id()] = recipe
                logger.debug(f"Added recipe '{recipe.name}/{recipe.version}'")

    return artifacts


def compute_dependencies_graph(artifacts):
    logger = logging.getLogger("complott")
    logger.info("Computing dependencies graph...")
    topological_sorter = graphlib.TopologicalSorter()

    for artifact in artifacts.values():
        if not isinstance(artifact, Recipe):
            continue
        for dependency in artifact.dependencies:
            topological_sorter.add(artifact.id(), dependency.artifact_id())

    return topological_sorter


def build_all(
    recipes_folder,
    build_folder,
    artifacts,
    dependencies_graph,
    override=False,
    num_jobs=1,
):
    logger = logging.getLogger("complott")
    if not os.path.exists(build_folder):
        os.mkdir(build_folder)

    failed_artifacts_ids = set()

    # task_queue = queue.Queue()
    dependencies_graph.prepare()
    while dependencies_graph.is_active():
        for artifact_id in dependencies_graph.get_ready():
            artifact = artifacts[artifact_id]

            if isinstance(artifact, Recipe):
                for dependency in artifact.dependencies:
                    dependency_artifact_id = dependency.artifact_id()
                    if dependency_artifact_id in failed_artifacts_ids:
                        logger.warning(
                            f"Skipped '{artifact_id}', dependency '{dependency_artifact_id}' failed."
                        )
                        failed_artifacts_ids.add(artifact_id)
                        dependencies_graph.done(artifact_id)
                        break
                if artifact_id in failed_artifacts_ids: continue
                        

            # task_queue.put(artifact)

            try:
                artifact.build(
                    recipes_folder, build_folder, artifacts, override=override
                )
            except Exception as e:
                logger.error(f"While building '{artifact_id}':\n ---> {e}")
                failed_artifacts_ids.add(artifact_id)

            dependencies_graph.done(artifact_id)
