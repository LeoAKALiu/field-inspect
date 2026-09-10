from glob import glob
from setuptools import find_packages, setup

package_name = "inspection_pipeline"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages() + ["astra_inspect_contract"],
    package_dir={"astra_inspect_contract": "../../../../packages/contracts/python/astra_inspect_contract"},
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/config", glob("config/*.yaml")),
        ("share/" + package_name + "/config", glob("config/*.json")),
        ("share/" + package_name + "/schema", ["../../docs/manifest.schema.json", "../../../../packages/contracts/inspection-package-v2.schema.json"]),
    ],
    install_requires=["setuptools", "bagit==1.9.0", "pydantic>=2.10,<3"],
    zip_safe=True,
    maintainer="SCOUT Mini Team",
    maintainer_email="maintainer@example.com",
    description="Run lifecycle, rosbag and manifest management for inspection demos.",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "inspection_acceptance_export = inspection_pipeline.acceptance_export:main",
            "inspection_bundle_export = inspection_pipeline.bundle_export:main",
            "inspection_camera_calibration = inspection_pipeline.camera_calibration:main",
            "inspection_manager = inspection_pipeline.inspection_manager:main",
            "inspection_pointcloud_export = inspection_pipeline.pointcloud_export:main",
            "inspection_route_teach = inspection_pipeline.route_teaching:main",
            "inspection_route_orchestrator = inspection_pipeline.route_orchestrator:main",
            "inspection_trajectory_export = inspection_pipeline.trajectory_export:main",
            "inspection_run_validate = inspection_pipeline.run_validation:main",
            "inspection_station_attempts_export = "
            "inspection_pipeline.station_attempts_export:main",
            "inspection_usb_finalize = inspection_pipeline.usb_finalize:main",
        ],
    },
    tests_require=["pytest"],
)
