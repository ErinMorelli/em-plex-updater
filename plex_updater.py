#!/usr/bin/env python
# Copyright (c) 2015-2021, Erin Morelli
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.

import re
import os
import sys
import time
import argparse
import subprocess
from xml.etree import ElementTree
from urllib.request import URLopener

import yaml
import requests

# Script global constants
API_ROOT_URL = 'https://plex.tv/'
DPKG_EXECUTABLE = '/usr/bin/dpkg'
RPM_EXECUTABLE = '/usr/bin/rpm'
CONFIG_FILE = os.path.join(os.path.expanduser('~'), '.config', 'plex-updater', 'config.yml')


class FileAction(argparse.Action):
    """Custom files validation action for argparse."""

    def __call__(self, parser, namespace, values, option_string=None):
        """Checks that file provided exists."""
        file_path = os.path.abspath(values)

        # Check that the path exists
        if not os.path.exists(file_path):
            error = f"File provided for {self.dest} does not exist: {values}"
            parser.error(error)

        # Set value in namespace object
        setattr(namespace, self.dest, values)


def get_args():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Checks to see if there's a new Plex Media Server version," +
            "then downloads and installs it."
        )
    )

    # Add args to parser
    parser.add_argument(
        '-f', '--config',
        default=CONFIG_FILE,
        help=f"Specify a configuration file the script to use. Default: {CONFIG_FILE}",
        action=FileAction
    )
    parser.add_argument(
        '-s', '--skip_install',
        default=False,
        help="Only downloads the new version file, does not install it.",
        action='store_true'
    )
    parser.add_argument(
        '-c', '--check_only',
        default=False,
        help="Only checks for newer version, does not download or install it.",
        action='store_true'
    )

    # Return parsed args
    return parser.parse_args()


def get_config(args):
    """Get extra info from config file."""
    config = yaml.load(open(args.config).read())

    # Check for valid system value
    if 'system_type' not in config.keys():
        sys.exit("Config value for 'system_type' must be defined")

    # Check for valid version value
    if 'system_os' not in config.keys():
        sys.exit("Config value for 'system_os' must be defined")

    # Check for valid download folder
    if not os.path.exists(config['folder']):
        sys.exit(f"Path to 'folder' does not exist: {config['folder']}")

    # Return config info
    return config


def get_token(config):
    """Sign in and get token from Plex."""
    sign_in_url = API_ROOT_URL + 'users/sign_in.json'

    # Make API post request to get token
    sign_in_resp = requests.post(
        sign_in_url,
        data={},
        headers={'X-Plex-Client-Identifier': config['client']},
        auth=(config['username'], config['password'])
    )

    # Get JSON response
    sign_in_json = sign_in_resp.json()

    # Check for error
    if 'error' in sign_in_json.keys():
        sys.exit(sign_in_json['error'])

    # Get JSON response
    sign_in_user = sign_in_json['user']

    # Check that user has Plex Pass access if they've enabled it
    if config['plex_pass']:
        subscription = sign_in_user['subscription']

        # See if plex pass needs to be disabled
        if subscription is None or \
                not subscription['active'] or \
                'pass' not in subscription['features']:
            # Disables use of the Plex Pass download feed
            config['plex_pass'] = False

            # Notify the user via CLI of the change
            print('WARNING: The account provided does not have a ' +
                  'valid Plex Pass subscription!\n\t Switching to use the ' +
                  'Public downloads feed for updates.', file=sys.stderr)

    # Return token from XML response
    return sign_in_user['authentication_token']


def get_server_info(config, args, token):
    """Get current server version."""
    server_url = API_ROOT_URL + 'pms/servers.xml'

    # Make API get request for server info
    server_resp = requests.get(
        server_url,
        headers={'X-Plex-Token': token}
    )

    # Get list of Plex servers from XML
    server_xml = None
    server_resp_xml = ElementTree.fromstring(server_resp.content)

    # Find the server we want to update
    for server in server_resp_xml:
        if server.get('machineIdentifier') == config['client']:
            server_xml = server
            break

    # Bail if we didn't find the Plex server we want
    if server_xml is None:
        msg = 'Could not find a Plex Media Server with client ID: {0}'
        sys.exit(msg.format(config['client']))

    # Make sure user is server owner
    if server_xml.get('owned') == '0':
        print('WARNING: The account provided does not have ownership ' +
              'permissions for this server!\n\t Updates can only ' +
              'be downloaded, not installed.', file=sys.stderr)

        # Enable 'skip_install'
        args.skip_install = True

    # Return server Plex version info
    return {
        'updated': int(server_xml.get('updatedAt')),
        'version': server_xml.get('version')
    }


def check_system_build(config, download_os):
    """Check system build and return release data."""
    if 'system_build' not in config.keys():
        release = download_os['releases'][0]
        print(f"Using first available {download_os['name']} build: {release['label']}")
        return release

    # Get system build from config
    system_build = config['system_build']

    # Find a release that matches the build
    for release in download_os['releases']:
        if re.match(system_build, release['label'], re.I):
            return release

    # Make sure release is valid
    builds = "\n".join([f" + {r['label']}" for r in download_os['releases']])
    sys.exit(f"Value for 'system_build' is not valid, must regex match one of:\n{builds}")


def get_download_info(config, token):
    """Get current Plex version."""
    downloads_url = API_ROOT_URL + 'api/downloads/1.json'

    # Set up download params
    download_params = {}

    # Set up Plex Pass feed, if active
    if config['plex_pass']:
        download_params['channel'] = 'plexpass'

    # Make API get request to downloads page
    downloads_resp = requests.get(
        downloads_url,
        params=download_params,
        headers={'X-Plex-Token': token}
    )

    # Get JSON content of API return
    downloads = downloads_resp.json()

    # Check for errors
    if 'error' in downloads.keys():
        sys.exit(downloads['error'])

    # Retrieve system type from config
    system_type = config['system_type'].lower()

    # Make sure system type is valid
    if system_type not in downloads.keys():
        types = ', '.join(downloads.keys())
        sys.exit(f"Value for 'system_type' not valid, must be one of: {types}")

    # Get system information
    systems = downloads[system_type]

    # Get system os from config
    system_os = config['system_os']

    # Find matching system OS
    download_os = None
    for available_os in systems.keys():
        if re.match(system_os, available_os, re.I):
            download_os = systems[available_os]
            break

    # Make sure system OS is valid
    if download_os is None:
        oses = ', '.join(systems.keys())
        sys.exit(f"Value for 'system_os' is not valid, must regex match one of: {oses}")

    # Get download release info
    download_release = check_system_build(config, download_os)

    return {
        'updated': download_os['release_date'],
        'version': download_os['version'],
        'link': download_release['url']
    }


def has_newer_version(server, download):
    """Check if Plex's version is newer than server version."""
    s_version = server['version'].split('.')
    d_version = download['version'].split('.')

    def compare_versions(server_, download_):
        """Iteratively compare server and download version ints."""
        try:
            s_int = server_.pop(0)
            d_int = download_.pop(0)
        except IndexError:
            # If we've reached the end of our arrays, return
            return False

        # If the numbers are the same, keep going
        if d_int == s_int:
            return compare_versions(server_, download_)
        # If the download's number is higher, it's a newer version
        if d_int > s_int:
            return True
        # If the server's number is higher, it's an older version
        if d_int < s_int:
            return False

    # Recursively compare versions
    return compare_versions(s_version, d_version)


def download_update(config, download):
    """Download and new Plex package."""
    download_name = f"pms_{download['version']}{os.path.splitext(download['link'])[1]}"
    download_target = os.path.join(config['folder'], download_name)

    # If we've already downloaded this file, remove it
    if os.path.exists(download_target):
        os.remove(download_target)

    # Download the file
    download_path = URLopener().retrieve(download['link'], download_target)

    # Make sure the file exists
    return os.path.exists(download_path[0]), download_path[0]


def install_update(config, package):
    """Installs the new Plex package."""
    if config['system_os'].lower() != 'linux':
        sys.exit('Sorry, installation is currently only available on Linux')

    # Get the package file extension
    package_ext = os.path.splitext(package)[1]

    try:
        # Install on Ubuntu with dpkg
        if re.match(r'\.deb$', package_ext, re.I) and os.path.exists(DPKG_EXECUTABLE):
            subprocess.check_output(f'{DPKG_EXECUTABLE} -i {package}', shell=True)
            return True

        # Install on Fedora or CentOS with rpm
        elif re.match(r'\.rpm$', package_ext, re.I) and os.path.exists(RPM_EXECUTABLE):
            subprocess.check_output(f'{RPM_EXECUTABLE} -Uhv {package}', shell=True)
            return True

    # Check for a failed install
    except subprocess.CalledProcessError:
        return False

    # Fall back to false
    return False


def main():
    """Main wrapper function to run script."""
    args = get_args()

    # Get config file info
    config = get_config(args)

    # Start program
    print(f'Checking for updates @{time.ctime()}')

    # Get token
    token = get_token(config)

    # Get server info
    server = get_server_info(config, args, token)
    print(f"Server Version: {server['version']}")

    # Get download info
    download = get_download_info(config, token)

    # Check for new version
    if has_newer_version(server, download):
        print(f"New version available: {download['version']}")

        # Bail here if we're not downloading or installing
        if args.check_only:
            return

        # Download the new Plex package
        (download_success, package) = download_update(config, download)

        # Bail here if we had problems with the download
        if not download_success:
            sys.exit('There was an problem downloading the new Plex version.')

        # Bail here if we're only downloading
        if args.skip_install:
            print(f'The new Plex version has been downloaded:\n{package}')
            return

        # Install the new Plex package
        install_completed = install_update(config, package)

        # Return an error if there was a problem with the installation
        if not install_completed:
            msg = f'There was an problem installing the new Plex version.\n' \
                  f'Try installing the package manually: {package}'
            sys.exit(msg)

        # Sleep 30 seconds before checking server
        print('Pause for 30 seconds while server updates...')
        time.sleep(30)

        # Check that install was actually successful
        new_server_info = get_server_info(config, args, token)

        # Verify new version
        if new_server_info['version'] != download['version']:
            msg = f'There was an problem installing the new Plex version.\n' \
                  f'Try installing the package manually: {package}'
            sys.exit(msg)

        # Otherwise, remove the package if we need tp
        if config['remove_completed']:
            os.remove(package)

        # Return success
        print(f"Plex has been successfully updated to version {download['version']}")

    else:
        # Otherwise, we have the latest version
        print('Plex is up-to-date!')


if __name__ == '__main__':
    # Run this thing
    main()
