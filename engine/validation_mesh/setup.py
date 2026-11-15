"""Validation Mesh 安装脚本。

用法
----
    cd engine/validation_mesh
    pip install .            # 本地安装
    # 或打包
    python setup.py sdist bdist_wheel

安装后可在任意项目：
    from validation_mesh import run_parity_loop, VirtualLabGuard, Calibrator
"""

from setuptools import setup, find_packages

setup(
    name="swarmlabs-validation-mesh",
    version="0.1.0",
    description="Virtual-experiment parity validation: GP surrogate + UQ + 3-state guard.",
    long_description=(
        "SwarmLabs Validation Mesh exposes the 'virtual experiment vs real "
        "experiment parity' toolkit as an installable package: GP surrogate "
        "with uncertainty quantification, a calibration meter (rmse / 95% CI "
        "coverage / calibration error), and a 3-state trust gate "
        "(PASS / CONTROLLED / REJECT). Pure numpy + scipy, no GPU, no LLM."
    ),
    long_description_content_type="text/plain",
    author="SwarmLabs",
    url="https://swarmlabs.tools",
    license="MIT",
    packages=find_packages(include=["validation_mesh", "validation_mesh.*"]),
    python_requires=">=3.9",
    install_requires=["numpy>=1.21", "scipy>=1.7"],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Topic :: Scientific/Engineering",
    ],
)
