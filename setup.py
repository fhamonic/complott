from setuptools import setup, find_packages

setup(
    name="complott",
    version="0.1.0",
    description="A simple CLI tool for greeting.",
    url="https://github.com/fhamonic/complott",
    author="François Hamonic",
    author_email="francois.hamonic@gmail.com",
    license="MIT",
    packages=find_packages(),
    include_package_data=True,
    py_modules=["complott"],
    install_requires=[
        "click",
    ],
    entry_points={
        "console_scripts": [
            "complott=complott.cli:cli",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.6",
)
