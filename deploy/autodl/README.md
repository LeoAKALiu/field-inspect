# AutoDL deployment

This deployment keeps the existing AutoDL `6008` service untouched and serves Digital Twin on
the platform-mapped HTTP port `6006`.

- Release root: `/root/autodl-tmp/digital-twin/releases/<git-sha>`
- Active symlink: `/root/autodl-tmp/digital-twin/current`
- Runtime: shared Python virtualenv plus Nginx
- API: `127.0.0.1:8000`
- Public gateway: `0.0.0.0:6006`
- Logs and PID files: `/root/autodl-tmp/digital-twin/runtime`

For the public release, `optimize_assets.py` can generate bandwidth-oriented display copies of
the tunnel mesh and point cloud. It never modifies the full-resolution LIRIS source assets in
the repository. Treat these generated copies as visualization assets, not survey-grade data.

```bash
python -m venv .asset-venv
.asset-venv/bin/pip install -r deploy/autodl/requirements-assets.txt
.asset-venv/bin/python deploy/autodl/optimize_assets.py \
  --source-root assets/tunnel/liris \
  --output-root /path/to/release/web/tunnel/liris
```

After an AutoDL instance reboot, restore the service with:

```bash
/root/autodl-tmp/digital-twin/deploy/start.sh
```

Check it with:

```bash
/root/autodl-tmp/digital-twin/deploy/status.sh
```
