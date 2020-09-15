import asyncio

import click
import maya
import aiofiles


from aioipfabric.filters import parse_filter

from ipfnetcfgbu.domain_remover import make_domain_remover
from ipfnetcfgbu.config_model import ConfigModel
from ipfnetcfgbu.ipf import IPFabricClient
from ipfnetcfgbu import logging
from .root import cli, opt_config_file, WithConfigCommand


def exec_backup(config: ConfigModel, opts):
    ipf_cfg = config.ipfabric

    log = logging.get_logger()

    log.info("Fetching inventory from IP Fabric")
    if ipf_cfg.filters:
        log.info(f"Using filter: {ipf_cfg.filters}")
        ipf_filters = parse_filter(ipf_cfg.filters)
    else:
        log.warning("No device filtering specified")
        ipf_filters = None

    ipf = IPFabricClient()

    loop = asyncio.get_event_loop()
    loop.run_until_complete(ipf.login())

    # obtain the device inventory that is matching the User provided filters.
    # This list of hostnames is used to filter the configuration files that are
    # requested.

    devices = loop.run_until_complete(
        ipf.fetch_devices(columns=["hostname"], filters=ipf_filters)
    )

    if not len(devices):
        log.warning("No devices matching filter")
        return

    hostnames = {rec["hostname"] for rec in devices}

    log.info(f"Inventory contains {len(hostnames)} devices")

    def device_filter(hashrec):
        return hashrec["hostname"] in hostnames

    date_start = opts["date_start"]

    if (date_end := opts["date_end"]) is None:
        date_end = date_start.snap("1d")

    since_ts = int(date_start.epoch * 1_000)
    before_ts = int(date_end.epoch * 1_000)

    timespan_str = f"{date_start}, {date_end}"

    if opts["all"] is True:
        log.info(f"Backup all configs: {timespan_str}")
    else:
        log.info(f"Backup configs that have changed: {timespan_str}")

    as_hostname = (
        make_domain_remover(domain_names=ipf_cfg.strip_hostname_domains)
        if ipf_cfg.strip_hostname_domains
        else lambda x: x
    )

    config_dir = config.defaults.configs_dir

    async def save_config(rec, config_text):
        hostname = as_hostname(rec["hostname"]).lower()
        log.info(f"SAVE CONFIG FOR: {hostname}")
        cfg_f = config_dir.joinpath(hostname + ".cfg")
        async with aiofiles.open(cfg_f, "w+") as ofile:
            await ofile.write(config_text)

    res = loop.run_until_complete(
        ipf.fetch_device_configs(
            since_ts=since_ts,
            before_ts=before_ts,
            on_config=save_config,
            device_filter=device_filter,
            all_configs=opts["all"],
            dry_run=opts["dry_run"],
        )
    )

    log.info(f"Total devices: {len(res)}")
    logging.stop()


def as_maya(ctx, param, value):
    if not value:
        return None

    try:
        dt = maya.when(value)
        return dt.snap("@d") if value == "today" else dt

    except ValueError as exc:
        ctx.fail(f"{exc.args[0][:-1]}: {value}")


@cli.command(name="backup", cls=WithConfigCommand)
@opt_config_file
@click.option(
    "--date-start",
    help="Identifies the starting timestamp date/time",
    metavar="<DATE-TIME>",
    callback=as_maya,
    default="today",
)
@click.option(
    "--date-end",
    help="Identifies the ending timestamp date/time",
    metavar="<DATE-TIME>",
    callback=as_maya,
)
@click.option(
    "--all", help="Backup all configs, not just those that changed", is_flag=True
)
@click.option(
    "--dry-run", help="Use to see device list that would be backed up", is_flag=True
)
@click.pass_context
def cli_backup(ctx, **opts):
    """
    Backup network configurations.
    """
    exec_backup(ctx.obj["config"], opts)