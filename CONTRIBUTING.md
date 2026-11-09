# Contributing

Thanks for helping. Bug reports, fixes and small focused features are welcome.

## Set up

```bash
git clone https://github.com/danialrp/isp-picker.git
cd isp-picker
bash scripts/build-agent.sh        # only needed for phone mode
python3 helper/isp_picker.py serve --verbose
```

Load `extension/` with **Load unpacked** at `chrome://extensions`. After you edit extension files, press the reload button on the extension card.

## Before you open a pull request

Run the same checks CI runs:

```bash
python3 -m py_compile helper/isp_picker.py
(cd agent && go vet ./... && GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -o /dev/null .)
for f in extension/*.js; do node --check --input-type=module < "$f"; done
python3 -c "import json; [json.load(open(f)) for f in ('extension/manifest.json', 'extension/_locales/en/messages.json', 'helper/config.example.json')]"
bash -n scripts/*.sh
```

Then test by hand (see [docs/TESTING.md](docs/TESTING.md) for the full plan):

1. `python3 helper/isp_picker.py list` shows your interfaces.
2. Pick a connection in the popup and open a few sites.
3. Press **System default** and check that Chrome works without the helper.

## Rules

- Keep the helper and the agent on their standard libraries. No new runtime dependencies.
- UI strings live in `extension/_locales/en/messages.json`, not in the HTML or JavaScript.
- One topic per pull request. Describe what changed and how you tested it.
- Add a line to `CHANGELOG.md` under **Unreleased**.
