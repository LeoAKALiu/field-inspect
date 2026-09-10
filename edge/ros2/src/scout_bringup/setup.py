from glob import glob
from setuptools import find_packages, setup

package_name = "scout_bringup"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
        ("share/" + package_name + "/config", glob("config/*.yaml")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="SCOUT Mini Team",
    maintainer_email="maintainer@example.com",
    description="Launch and configuration package for the SCOUT Mini demo stack.",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "validate_config = scout_bringup.config_validator:main",
            "preflight_check = scout_bringup.preflight:main",
            "health_monitor = scout_bringup.health_monitor:main",
            "localization_adapter = scout_bringup.localization_adapter:main",
        ],
    },
    tests_require=["pytest"],
)
