PYTHON ?= python3.12
OPENCLAW ?= OpenClaw

.PHONY: test smoke audience-smoke three-channel-smoke preflight openclaw-preflight

test:
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py' -v

smoke:
	rm -rf /tmp/network-chief-smoke
	NETWORK_CHIEF_NO_DASHBOARD=1 NETWORK_CHIEF_DB=/tmp/network-chief-smoke/network.db $(PYTHON) -m network_chief init
	NETWORK_CHIEF_NO_DASHBOARD=1 NETWORK_CHIEF_DB=/tmp/network-chief-smoke/network.db $(PYTHON) -m network_chief add-goal --title "Reactivate investor network" --cadence weekly --capital-type financial --target-segment investor --success-metric "5 warm conversations"
	NETWORK_CHIEF_NO_DASHBOARD=1 NETWORK_CHIEF_DB=/tmp/network-chief-smoke/network.db $(PYTHON) -m network_chief import-linkedin --file examples/linkedin_connections_sample.csv
	NETWORK_CHIEF_NO_DASHBOARD=1 NETWORK_CHIEF_DB=/tmp/network-chief-smoke/network.db $(PYTHON) -m network_chief import-gmail-json --file examples/gmail_sample.json --mailbox-owner you@example.com
	NETWORK_CHIEF_NO_DASHBOARD=1 NETWORK_CHIEF_DB=/tmp/network-chief-smoke/network.db $(PYTHON) -m network_chief brief --limit 3 --out /tmp/network-chief-smoke/today.md
	NETWORK_CHIEF_NO_DASHBOARD=1 NETWORK_CHIEF_DB=/tmp/network-chief-smoke/network.db $(PYTHON) -m network_chief mindmap --out /tmp/network-chief-smoke/network-map.json

audience-smoke:
	rm -rf /tmp/network-chief-audience-smoke
	NETWORK_CHIEF_DB=/tmp/network-chief-audience-smoke/network.db $(PYTHON) -m network_chief init
	NETWORK_CHIEF_DB=/tmp/network-chief-audience-smoke/network.db $(PYTHON) -m network_chief add-goal --title "Grow AI operator audience" --cadence weekly --capital-type competence --target-segment "AI operators, builders, founders" --success-metric "3 high-signal public conversations"
	NETWORK_CHIEF_DB=/tmp/network-chief-audience-smoke/network.db $(PYTHON) -m network_chief import-linkedin --file examples/linkedin_connections_sample.csv
	NETWORK_CHIEF_DB=/tmp/network-chief-audience-smoke/network.db $(PYTHON) -m network_chief import-x --file examples/x_community_sample.json --owner-handle andrey
	NETWORK_CHIEF_DB=/tmp/network-chief-audience-smoke/network.db $(PYTHON) -m network_chief sync-sources --scan-dir examples --out /tmp/network-chief-audience-smoke/source-sync.md
	NETWORK_CHIEF_DB=/tmp/network-chief-audience-smoke/network.db $(PYTHON) -m network_chief audience-brief --topic "AI operator audience" --limit 3 --out /tmp/network-chief-audience-smoke/audience.md
	NETWORK_CHIEF_DB=/tmp/network-chief-audience-smoke/network.db $(PYTHON) -m network_chief record-draft-event --id "$$(NETWORK_CHIEF_DB=/tmp/network-chief-audience-smoke/network.db $(PYTHON) -m network_chief drafts | awk 'NR==1 {print $$1}')" --event approve --reason-code good_timing
	NETWORK_CHIEF_DB=/tmp/network-chief-audience-smoke/network.db $(PYTHON) -m network_chief record-audience-metric --channel x --metric-type replies --value 1 --note "Smoke metric"
	NETWORK_CHIEF_DB=/tmp/network-chief-audience-smoke/network.db $(PYTHON) -m network_chief scorecard --days 7 --out /tmp/network-chief-audience-smoke/scorecard.md

three-channel-smoke:
	rm -rf /tmp/network-chief-three-channel-smoke
	NETWORK_CHIEF_DB=/tmp/network-chief-three-channel-smoke/network.db $(PYTHON) -m network_chief init
	NETWORK_CHIEF_DB=/tmp/network-chief-three-channel-smoke/network.db $(PYTHON) -m network_chief add-goal --title "Grow AI operator audience" --cadence weekly --capital-type competence --target-segment "AI operators, builders, founders" --success-metric "3 useful conversations"
	NETWORK_CHIEF_DB=/tmp/network-chief-three-channel-smoke/network.db $(PYTHON) -m network_chief import-linkedin --file examples/linkedin_connections_sample.csv
	NETWORK_CHIEF_DB=/tmp/network-chief-three-channel-smoke/network.db $(PYTHON) -m network_chief sync-gmail --file examples/gmail_sample.json --mailbox-owner you@example.com --out /tmp/network-chief-three-channel-smoke/gmail-sync.md
	NETWORK_CHIEF_DB=/tmp/network-chief-three-channel-smoke/network.db $(PYTHON) -m network_chief import-x --file examples/x_community_sample.json --owner-handle andrey
	NETWORK_CHIEF_DB=/tmp/network-chief-three-channel-smoke/network.db $(PYTHON) -m network_chief maintain-values
	NETWORK_CHIEF_DB=/tmp/network-chief-three-channel-smoke/network.db $(PYTHON) -m network_chief voice-profile rebuild --out /tmp/network-chief-three-channel-smoke/voice.md
	NETWORK_CHIEF_DB=/tmp/network-chief-three-channel-smoke/network.db $(PYTHON) -m network_chief prepare-daily-linkedin-post --industry energy --asset-dir /tmp/network-chief-three-channel-smoke --out /tmp/network-chief-three-channel-smoke/linkedin-daily-post.md
	NETWORK_CHIEF_DB=/tmp/network-chief-three-channel-smoke/network.db $(PYTHON) -m network_chief audience-brief --topic "AI operator audience" --limit 3 --out /tmp/network-chief-three-channel-smoke/audience.md
	NETWORK_CHIEF_DB=/tmp/network-chief-three-channel-smoke/network.db $(PYTHON) -m network_chief prepare-channel-drafts --channels gmail,linkedin,telegram --limit 3 > /tmp/network-chief-three-channel-smoke/channel-drafts.txt
	NETWORK_CHIEF_DB=/tmp/network-chief-three-channel-smoke/network.db $(PYTHON) -m network_chief record-draft-event --id "$$(NETWORK_CHIEF_DB=/tmp/network-chief-three-channel-smoke/network.db $(PYTHON) -m network_chief drafts | awk 'NR==1 {print $$1}')" --event approve --reason-code good_timing
	NETWORK_CHIEF_DB=/tmp/network-chief-three-channel-smoke/network.db $(PYTHON) -m network_chief next-actions --no-gbrain --limit 5 --out /tmp/network-chief-three-channel-smoke/next-actions.md
	NETWORK_CHIEF_DB=/tmp/network-chief-three-channel-smoke/network.db $(PYTHON) -m network_chief record-engagement-outcome --draft-id "$$(NETWORK_CHIEF_DB=/tmp/network-chief-three-channel-smoke/network.db $(PYTHON) -m network_chief drafts --status approved | awk 'NR==1 {print $$1}')" --outcome useful_conversation --note "Smoke outcome"
	NETWORK_CHIEF_DB=/tmp/network-chief-three-channel-smoke/network.db $(PYTHON) -m network_chief scorecard --days 7 --out /tmp/network-chief-three-channel-smoke/scorecard.md
	NETWORK_CHIEF_DB=/tmp/network-chief-three-channel-smoke/network.db $(PYTHON) -m network_chief sync-gbrain --since-days 7 --dry-run

preflight: openclaw-preflight

openclaw-preflight:
	$(OPENCLAW) config validate
	$(OPENCLAW) channels status --probe
	$(OPENCLAW) models status
	$(OPENCLAW) agents list
