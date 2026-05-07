# Maxwell REST CLI

cli/tui for DESY Maxwell Slurm REST access.

You'll need the following

- Either the DESY eduvpn with access to `https://max-slurm-rest.desy.de`, or be on the DESY network.
- the macOS Keychain command: `security`
- `curl`
- `jq`
- `python3` for the TUI

You may need to install `jq`,

```sh
brew install jq
```

First run through the setup commands in order to get a token

```sh
./bin/maxwell init --user <desy-user>
./bin/maxwell auth login
./bin/maxwell auth refresh
```

To call it as `maxwell` add this repository's `bin` directory to your shell `PATH` or symlink `bin/maxwell` into a directory that is already on `PATH`. `init` writes non-secret defaults to ~/.config/maxwell-rest/config.env. Tokens are stored in macOS Keychain under:

```text
maxwell-rest.portal-token
maxwell-rest.slurm-token
```

### How to submit jobs and monitor

You can submit a job like so:

```sh
./bin/maxwell submit job.sh --name testapi --partition allcpu --time 1000 --cpus 1 --tasks 1 --mem 1000
```

Submit defaults are intentionally small: one task, one CPU per task, and 1000 MB memory. Use `--cpus`, `--tasks`, `--mem`, `--account`, and `--output` when a job needs different resources or explicit accounting/output paths.

Maxwell partition policy may still allocate or bill a whole node even when the request asks for one CPU and modest memory. Check `tres_req_str` and `tres_alloc_str` in `maxwell job <jobid> --json` when validating a partition.

Preview the exact Slurm REST payload without submitting:

```sh
./bin/maxwell submit job.sh --name testapi --time 1000 --dry-run
```

You can list your running/queue jobs like so:

```sh
./bin/maxwell jobs
./bin/maxwell jobs --json
```

You can inspect or cancel a job like so:

```sh
./bin/maxwell job <jobid>
./bin/maxwell cancel <jobid>
./bin/maxwell history <jobid>
./bin/maxwell watch <jobid>
./bin/maxwell doctor
```

### TUI

After logging in and refreshing a token, start the job monitor:

```sh
./bin/maxwell-tui
```

Keys:

```text
r      refresh jobs
s      submit a script
j/down select next job
k/up   select previous job
Enter  load selected job details
h      load selected job history
c      cancel selected job after confirmation
q      quit
```

Note that this tool is REST-only. It embeds the submitted shell script in the Slurm REST payload, but it does not copy local files to Maxwell or retrieve logs. Thus, its important that scripts refer to files already present on Maxwell/Dust storage, otherwise it'll fail.
