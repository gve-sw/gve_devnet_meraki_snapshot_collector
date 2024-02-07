"""
Copyright (c) 2024 Cisco and/or its affiliates.
This software is licensed to you under the terms of the Cisco Sample
Code License, Version 1.1 (the "License"). You may obtain a copy of the
License at
https://developer.cisco.com/docs/licenses
All use of the material herein must be in accordance with the terms of
the License. All rights not expressly granted by the License are
reserved. Unless required by applicable law or agreed to separately in
writing, software distributed under the License is distributed on an "AS
IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
or implied.
"""

import http.server
import os
import socketserver
import sys
from datetime import datetime
from time import sleep

import meraki
import requests
import typer
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader
from meraki.exceptions import APIError
from rich import print
from rich.console import Console
from rich.panel import Panel
from rich.progress import track
from rich.prompt import Confirm, Prompt
from typing_extensions import Annotated

console = Console()

load_dotenv()


def getOrgs(dashboard: meraki.DashboardAPI) -> str:
    """
    Get Meraki organizations and prompt user to select one
    """
    with console.status("Connecting to Meraki...."):
        try:
            orgs = dashboard.organizations.getOrganizations()
        except APIError as e:
            print("\r\n[red]Failed to connect to Meraki. Error:")
            print(f"[red]{e.message['errors'][0]}")
            sys.exit(1)
    print("[green]Connected to Meraki dashboard!")
    print(f"Found {len(orgs)} organization(s).\r\n")

    # If one org, return early
    if len(orgs) == 1:
        print(f"Working with Org: {orgs[0]['name']}")
        return orgs[0]["id"]

    # Else, ask which org to use
    print("Available organizations:")
    org_names = [org["name"] for org in orgs]
    for org in orgs:
        print(f"- {org['name']}")

    print()
    selection = Prompt.ask(
        "Which organization should we use?", choices=org_names, show_choices=False
    )
    for org in orgs:
        if org["name"] == selection:
            return org["id"]


def getCameraSerials(dashboard: meraki.DashboardAPI, org_id: str) -> dict:
    """
    Collect a list of Meraki MV camera serial numbers
    """
    device_list = dashboard.organizations.getOrganizationDevices(
        org_id, productTypes="camera"
    )
    cameras = {}
    for device in track(device_list):
        cameras[device["serial"]] = {}
        cameras[device["serial"]]["name"] = device["name"]
    print(f"Found {len(cameras.keys())} cameras in the organization.")

    if len(cameras.keys()) == 0:
        print("[red]No cameras found. Exiting.")
        sys.exit(1)

    return cameras


def collectSnapshots(
    dashboard: meraki.DashboardAPI, cameras: dict, time: datetime, outputdir: str
):
    """
    Collect snapshots from Meraki MV cameras for a given timestamp
    """
    bad_cam = []
    remove = []
    print("Requesting snapshots from cameras...")
    for serial, details in track(cameras.items()):
        name = details["name"]
        if name == "":
            name = "No Name Set"
        # If no timestamp, assume current time
        if time is None:
            try:
                result = dashboard.camera.generateDeviceCameraSnapshot(serial)
            except APIError:
                result = None
        else:
            try:
                result = dashboard.camera.generateDeviceCameraSnapshot(
                    serial, timestamp=time.isoformat()
                )
            except APIError:
                result = None
        if result:
            cameras[serial]["snapshot"] = result["url"]
        else:
            bad_cam.append(f"{name} / {serial}")
            remove.append(serial)

    print("[green]Done!")
    print()

    if len(bad_cam) > 0:
        print("[red]Failed to request snapshots from the following cameras:")
        for cam in bad_cam:
            print(f"> [red]{cam}")
        for cam in remove:
            del cameras[cam]

    if len(bad_cam) == len(cameras.keys()):
        print("[red]No snapshots could be collected. Exiting.")
        sys.exit(1)

    print()
    with console.status("Waiting for snapshots to be ready..."):
        sleep(15)

    print("Collecting snapshots...")

    # Create output directory if needed
    exists = os.path.exists(outputdir)
    if not exists:
        os.makedirs(outputdir)

    for name, cam in track(cameras.items()):
        if cam["snapshot"] is not None:
            snapshot = requests.get(cam["snapshot"]).content
            with open(f"{outputdir}/{name}.jpg", "wb") as f:
                f.write(snapshot)
    print("[green]Done!")

    return cameras


def renderWeb(cameras: dict, outputdir: str):
    """
    Render an HTML report of collected snapshots
    """
    print("Building web report...")
    environment = Environment(loader=FileSystemLoader("templates/"))
    template = environment.get_template("base.html")

    content = template.render(path=outputdir, cameras=cameras)

    with open("./index.html", "w") as f:
        f.write(content)

    print("[green]Done!")


def cli(
    apikey: Annotated[
        str,
        typer.Option(
            "--apikey",
            "-k",
            prompt="Meraki dashboard API key",
            hide_input=True,
            envvar="MERAKI_DASHBOARD_API_KEY",
            help="Meraki dashboard API key",
        ),
    ],
    time: Annotated[
        datetime,
        typer.Option(
            "--time",
            "-t",
            help="Date & time to collect snapshots for",
        ),
    ] = None,
    outputdir: Annotated[
        str,
        typer.Option(
            "--outputdir",
            "-o",
            help="Output directory",
            rich_help_panel="Additional Options",
        ),
    ] = "snapshots",
    outputhtml: Annotated[
        bool,
        typer.Option(
            "--outputhtml",
            "-h",
            help="Output HTML report",
            rich_help_panel="Additional Options",
        ),
    ] = False,
):
    """
    Run bulk collection of snapshots from Meraki MV cameras

    If date & time are not specified, then script will collect snapshots for the current date & time
    """

    print()
    print(Panel.fit("  -- Start --  "))
    print("Running with the following parameters:")
    print(f"Output directory: {outputdir}")
    print(f"Output HTML report: {outputhtml}")
    if time is None:
        print("Date / Time: Now")
    else:
        print(f"Date / Time: {time}")

    print()
    print(Panel.fit("Connect to Meraki", title="Step 1"))
    dashboard = meraki.DashboardAPI(
        suppress_logging=True, caller="MVSnapshotCollector CiscoGVEDevNet"
    )
    org_id = getOrgs(dashboard)

    print()
    print(Panel.fit("Collect Camera Serial Numbers", title="Step 2"))
    cameras = getCameraSerials(dashboard, org_id)

    print()
    print(Panel.fit("Collect Snapshots", title="Step 3"))
    collectSnapshots(dashboard, cameras, time, outputdir)

    if outputhtml:
        print()
        print(Panel.fit("Render Web Report", title="Step 4"))
        renderWeb(cameras, outputdir)
        print()
        print("File saved to [green]index.html")

        if Confirm.ask("Start a web server to view the report?"):
            with socketserver.TCPServer(
                ("", 8080), http.server.SimpleHTTPRequestHandler
            ) as httpd:
                print("Local web server started at http://localhost:8080/")
                print("Use Ctrl-C to stop web server.")
                try:
                    httpd.serve_forever()
                except KeyboardInterrupt:
                    print("\r\n[red]Exiting...")
                    sys.exit(0)


if __name__ == "__main__":
    typer.run(cli)
