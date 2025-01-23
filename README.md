# ![ComPlotT](misc/complott_title.png)

ComPlotT (Community-driven Plotting Tool) generates plots using a recipe system to automatically download data from official sources and manage dependencies between recipes to share processed data. Recipes are run in constrained Docker containers for security and reproducibility.

Work in progress, Come Plot soon !

## Requirements: Python, Pip and Docker

### Linux (Ubuntu)

    sudo apt update
    sudo apt install python3 python3-pip docker

### Windows

Download and install Docker : https://docs.docker.com/desktop/setup/install/windows-install/

Download and install Python : https://www.python.org/downloads/

## Install

    git clone https://github.com/fhamonic/complott.git && cd complott
    pip install .

## Usage

    git clone https://github.com/fhamonic/complott_recipes.git
    complott build ./complott_recipes --build-folder=./build

