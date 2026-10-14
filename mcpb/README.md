# Desktop extension bundle

Builds Twin Me Up as an [MCPB](https://github.com/modelcontextprotocol/mcpb) desktop extension for
Claude Desktop, Smithery and other MCPB hosts. It uses the `uv` server type: the host installs the
published `twin-me-up` package with uv, so the same bundle works on every platform.

```bash
rsvg-convert -w 512 -h 512 mcpb/icon.svg -o mcpb/icon.png   # after changing the icon
npx --yes @anthropic-ai/mcpb@2.1.2 validate mcpb/manifest.json
npx --yes @anthropic-ai/mcpb@2.1.2 pack mcpb dist/twin-me-up-<version>.mcpb
```

Release automation builds the bundle and attaches it to each GitHub release. Keep the versions in
`manifest.json` and `pyproject.toml` equal to the package version.
