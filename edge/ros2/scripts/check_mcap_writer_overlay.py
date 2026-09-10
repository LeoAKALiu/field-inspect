#!/usr/bin/env python3
"""Check native MCAP splits with idle channels; no ROS nodes or hardware."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from ament_index_python.packages import get_package_share_directory
import rosbag2_py
from rclpy.serialization import serialize_message
from std_msgs.msg import String


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='new directory for synthetic evidence')
    args = parser.parse_args()
    doctor = shutil.which('mcap')
    if doctor is None:
        parser.error('mcap CLI is required')
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    config = Path(get_package_share_directory('inspection_pipeline')) / 'config/mcap_writer_options.yaml'
    writer = rosbag2_py.SequentialWriter()
    writer.open(rosbag2_py.StorageOptions(
        uri=str(root / 'bag'), storage_id='mcap', max_bagfile_duration=1,
        storage_preset_profile='zstd_fast', storage_config_uri=str(config)),
        rosbag2_py.ConverterOptions('', ''))
    libraries = sorted({line.split()[-1] for line in Path('/proc/self/maps').read_text().splitlines()
                        if '/libmcap.so' in line})
    for topic, msg_type in [('/active', 'String'), ('/latched_like', 'String'),
                            ('/never_published', 'Int32')]:
        writer.create_topic(rosbag2_py.TopicMetadata(
            name=topic, type='std_msgs/msg/' + msg_type, serialization_format='cdr'))
    message = serialize_message(String(data='native offline split test'))
    inputs = [('/latched_like', 1_000_000_000), ('/active', 1_001_000_000),
              ('/active', 3_000_000_000), ('/active', 5_000_000_000)]
    for topic, stamp in inputs:
        writer.write(topic, message, stamp)
    del writer
    checks = []
    for file in sorted((root / 'bag').glob('*.mcap')):
        result = subprocess.run([doctor, 'doctor', str(file)], capture_output=True, text=True)
        checks.append({'file': file.name, 'exit_code': result.returncode,
                       'stdout': result.stdout, 'stderr': result.stderr})
    reader = rosbag2_py.SequentialReader()
    reader.open(rosbag2_py.StorageOptions(uri=str(root / 'bag'), storage_id='mcap'),
                rosbag2_py.ConverterOptions('', ''))
    received = []
    while reader.has_next():
        topic, data, stamp = reader.read_next()
        received.append((topic, hashlib.sha256(data).hexdigest(), stamp))
    expected = [(topic, hashlib.sha256(message).hexdigest(), stamp) for topic, stamp in inputs]
    valid = len(checks) == 3 and all(item['exit_code'] == 0 for item in checks) and received == expected
    report = {'valid': valid, 'scope': 'synthetic_native_writer_only',
              'loaded_mcap_libraries': libraries, 'doctor': checks,
              'exact_messages_preserved': received == expected, 'messages': received}
    (root / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if valid else 1


if __name__ == '__main__':
    raise SystemExit(main())
