.PHONY: test smoke

test:
	python3 -m unittest discover -s tests -p 'test_*.py' -v

smoke:
	rm -rf /tmp/network-chief-smoke
	NETWORK_CHIEF_DB=/tmp/network-chief-smoke/network.db python3 -m network_chief init
	NETWORK_CHIEF_DB=/tmp/network-chief-smoke/network.db python3 -m network_chief add-goal --title "Reactivate investor network" --cadence weekly --capital-type financial --target-segment investor --success-metric "5 warm conversations"
	NETWORK_CHIEF_DB=/tmp/network-chief-smoke/network.db python3 -m network_chief import-linkedin --file examples/linkedin_connections_sample.csv
	NETWORK_CHIEF_DB=/tmp/network-chief-smoke/network.db python3 -m network_chief import-gmail-json --file examples/gmail_sample.json --mailbox-owner you@example.com
	NETWORK_CHIEF_DB=/tmp/network-chief-smoke/network.db python3 -m network_chief brief --limit 3 --out /tmp/network-chief-smoke/today.md
	NETWORK_CHIEF_DB=/tmp/network-chief-smoke/network.db python3 -m network_chief mindmap --out /tmp/network-chief-smoke/network-map.json
