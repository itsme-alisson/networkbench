# NetworkBench

A local toolkit for network engineers. Render templates, parse device output, validate data, and convert between JSON and YAML—all from your browser.

Your data stays on your computer. No account required, no analytics, and no cloud services. The first build needs internet access; after that, the tools work offline.

## Tools

| Tool | What it does |
| --- | --- |
| Jinja | Render templates with JSON or YAML data, including network filters for IP addresses, subnets, and VLANs. |
| TextFSM | Turn device output into structured data. |
| TTP | Extract data from text using templates. |
| XPath | Query XML using XPath expressions. |
| JSON | Validate and format JSON. |
| JSON ↔ YAML | Convert between the two formats. |

## Get started

Install and start Docker Desktop, or Docker Engine with Compose. From the project folder, run:

```sh
docker compose up --build -d
```

Open **[NetworkBench](http://127.0.0.1:8000)** in your browser.

<details>
<summary>Running on Linux?</summary>

Prepare the local data folder before starting:

```sh
mkdir -p data
sudo chown 10001:10001 data
sudo chmod 700 data
```

</details>

## Using NetworkBench

- Choose a tool using the buttons at the top.
- Paste your data and template. Results update as you type.
- Pick an **Example** to try a tool. Examples are saved only after you edit them.
- Give your workspace a name to make it easier to find later.
- Use **Previous work** to search saved work for each tool, or **Open latest** to pick up where you left off.

Jinja includes network filters such as `ipaddr`, `ipsubnet`, and `vlan_parser`. The **i** icon beside its description shows the available filters and installed modules.

## Your saved work

Edits save automatically to `data/workspaces/` in the project folder. Saved work stays there when you stop or rebuild the containers. There is no login; anyone using the app on your computer shares the same saved work.

**Delete saved** removes the selected workspace and its files. **Clean up all** permanently removes all saved workspaces and clears current drafts. Both ask for confirmation.

## Stop or restart

Stop the app:

```sh
docker compose down
```

Start it again without downloads:

```sh
docker compose up -d --no-build --pull never
```
