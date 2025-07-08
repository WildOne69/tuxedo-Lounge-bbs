#!/bin/env python3
#
# Generates a summary report of dial-up modem calls from a Qmodem capture log
# to help indicate call quality and download performance.
#
# This gathers things such as:
#  - Number of calls made, handshake and download success rates
#  - Inital/ending connect speed
#  - Time to handshake, download speed (CPS), call duration
#  - Modem audio round trip delay time (ms) when a US Robotics V.34 modem is used
#
# This works in conjunction with the Qmodem script at:
#   https://github.com/bwann/bbs/blob/main/qmodem/DL.SCR
#
# The Qmodem script dials into a Wildcat! BBS, starts a download of a specified
# test file (usually 64 KB in size), and captures the results of the call. The
# script emits some extra logging messages with timestamps in the form of
#
# "### label MM-DD-YY HH:MM:SS"
#
# which are parsed by this script.
#
# Example labels script looks for:
#   start_qmodem - When Qmodem is first started, used to zero out counters
#                  and save any old data a previous failed call.
#   start_dial - When the script starts dialing the BBS
#   connected - When the BBS answers and we have a connection
#   start_download - When the script starts the download
#   end_download - When the download finished, either successfully or not
#   aborting - Something has gone wrong and we're ending the call
#   stats_ati6 / end_stats_ati6 - Post-call statistics from the local modem
#
# Initial connect BPS is gathered from the BBS welcome banner upon connection,
# ending connect BPS is gathered from the ATI6 command output at the end of
# the call.
#
# There is a rich set of data from the ATI6/Y11/V commands that can be further
# analyzed, but for now this script only extracts a few key values and does not
# save the data.
#
# For debugging I left most of of the print statements I used in the code,
# uncomment as needed.
#

import argparse
import numpy as np
import re
import statistics
import sqlite3
from collections import Counter
from datetime import datetime
from enum import Enum
from pprint import pprint
from typing import Dict, List, Optional
from dataclasses import dataclass, field

def parse_log_ts(ts: str) -> datetime:
    return datetime.strptime(ts, '%m-%d-%y %H:%M:%S') 

class ConnectionType(Enum):
    MODEM = 1
    DIRECTSERIAL = 2 

@dataclass
class CallRecord:

    connected: Optional[bool] = None
    count_start_dial: int = 0
    count_connected: int = 0
    count_download_failure: int = 0
    count_download_success: int = 0

    # timing information
    start_qmodem: Optional[str] = None
    start_dial: Optional[str] = None
    connect_time: Optional[str] = None
    start_download: Optional[str] = None
    end_download: Optional[str] = None
    download_success: Optional[bool] = None
    end_call: Optional[str] = None
    exit_qmodem: Optional[str] = None
    aborted_time: Optional[str] = None

    # other data during call
    abort_reason: Optional[str] = None
    connect_type: Optional[int] = ConnectionType.MODEM
    remote_connect_bps: Optional[int] = None
    remote_reliable: Optional[bool] = None
    remote_ansi: Optional[bool] = None
    download_cps: Optional[int] = None

    # end-of-call modem data
    ati6: Optional[str] = None
    ati11: Optional[str] = None
    aty11: Optional[str] = None
    atv: Optional[str] = None

    def __post_init__(self):
        CallRecord.count_start_dial += 1

    def mark_connected(self, success: bool):
        self.connected = success
        if success:
          CallRecord.count_connected += 1

    def mark_download_success(self, success: bool):
        if success:
          CallRecord.count_download_success += 1
          self.download_success = True
        else:
          CallRecord.count_download_failure += 1
          self.download_success = False

    @classmethod
    def connect_failure_count(cls) -> int:
        return cls.count_start_dial - cls.count_connected

    @classmethod
    def connect_success_percent(cls) -> float:
        if cls.count_start_dial == 0:
            return 0.0
        return (cls.count_connected / cls.count_start_dial) * 100

    def termination_reason(self) -> Optional[str]:
      if self.aborted_time:
            aborted_after = int((self.aborted_time - self.connect_time).total_seconds())
            return f'ABORTED after {aborted_after} sec.'
      elif self.end_call:
            return 'normal'
      else:
            return 'unknown'

    def call_duration(self) -> Optional[int]:
        """
        Returns the total duration of the call in whole seconds. If a modem
        connection this is calculated from the start of diaing to the end of
        the call, or to abort time, or when qmodem is exited in an error
        condition. For direct serial connection this is calculated from start
        of qmodem to when qmodem is exited.

        Returns:
            int: call duration, or None if timestamps are missing.
        """
        if self.connect_type == ConnectionType.DIRECTSERIAL:
            # there is no dialing, just use the time we started and exited the app
            return int((self.exit_qmodem - self.start_qmodem).total_seconds())
        else:
            # we gracefully ended the call
            if self.end_call:
                return int((self.end_call - self.connect_time).total_seconds())
            # we aborted
            if self.aborted_time:
                return int((self.aborted_time - self.connect_time).total_seconds())
            # we crashed or script exited or something, just use the time
            # the program exited
            elif self.exit_qmodem:
                return int((self.exit_qmodem - self.connect_time).total_seconds())
            # fallthrough, no idea what happened and have no data
            else:
                return None

    def download_duration(self) -> Optional[int]:
        """
        Returns the total duration of the test download in whole seconds. This
        is only calcualted if the download was successful.
    
        Returns:
            int: download duration, or None if download was a failure.
        """
        if self.download_success:
            return int((self.end_download - self.start_download).total_seconds())
        return None

    def download_success_msg(self) -> Optional[str]:
        if self.download_success is True:
            return 'SUCCESS'
        elif self.download_success is False:
            return 'FAILED'
        else:
            return None

    def handshake_duration(self) -> Optional[int]:
        if self.connect_type == ConnectionType.DIRECTSERIAL:
            return 0
        if self.start_dial and self.connect_time:
            return int((self.connect_time - self.start_dial).total_seconds())
        return None

class CallRecordRepository:
    """
    Not implemented yet, intended to write to sqlite

    """
    def __init__(self, db_path='calls.db'):
        # self.conn = sqlite3.connect(db_path)
        # self._create_table()
        pass

    def _create_table(self):
        pass

    def save(self, record: CallRecord):
        pprint(record.__dict__)

class CallSessionStore:
    def __init__(self):
        self.records = []

    def save(self, record: CallRecord):
        self.records.append(record)

    def all(self):
        return self.records

    def durations(self, extractor_lambda) -> list[int]:
        results = []

        for record in self.records:
            value = extractor_lambda(record)
            if value is not None:
                results.append(value)

        return results

    def test(self) -> list[int]:
        pprint(self.durations(lambda r: getattr(r.ati6, "speed", None) if r.ati6 else None))

    def report_aggregates(self):

        termination_reasons = Counter()
        for r in self.records:
            reason = r.termination_reason()
            if reason.startswith("normal"):
                termination_reasons["normal"] += 1
            else:
                termination_reasons["aborted_unknown"] += 1


        # chatgpt came up with this function, some lambda magic
        def stats(name, values):
            if not values:
                print(f"{name:20}: N/A")
                return
            print(f"{name:20}: avg={sum(values)//len(values):>5}  min={min(values):>5}  max={max(values):>5}  p95={int(np.percentile(values, 95)):>5}")

        download_success_pct = 0
        if CallRecord.count_download_failure == 0:
            download_success_pct = 100.0
        else:
            download_success_pct = (CallRecord.count_download_success /
                            (CallRecord.count_download_success + CallRecord.count_download_failure)) * 100

        print(f"Total calls: %d" % CallRecord.count_start_dial)
        print(f"Download success/failures:  {CallRecord.count_download_success} / {CallRecord.count_download_failure}, success: {download_success_pct:.2f}%")
        print(f"Termination reasons: %d normal goodbye, %d aborted/lost" % (termination_reasons['normal'], termination_reasons['aborted_unknown']))

        stats("Initial connect BPS:", self.durations(lambda r: r.remote_connect_bps))
        stats("Ending connect BPS: ", self.durations(lambda r: getattr(r.ati6, "speed", None) if r.ati6 else None))
        stats("Time to connect:    ", self.durations(lambda r: r.handshake_duration()))
        stats("Download CPS        ", self.durations(lambda r: r.download_cps))
        stats("Call duration       ", self.durations(lambda r: r.call_duration()))
        stats("Roundtrip delay     ", self.durations(lambda r: getattr(r.ati11, 'roundtrip_delay', None)))


class ATIParserBase:
    def __init__(self, input_lines: List[str]):
        self.input_lines = input_lines
        self.mapping = self.get_mapping()
        self.data = self.parse_output()

    def get_mapping(self) -> Dict[str, str]:
        raise NotImplementedError

    def get_data(self) -> Dict[str, Optional[int | str]]:
        return self.data

    def mapped_name(self, in_key: str) -> Optional[str]:
        return self.mapping.get(in_key)

    def parse_output(self) -> Dict[str, Optional[int | str]]:
        if not self.input_lines:
            return{}

        data = {}
        for line in self.input_lines:
            if re.match(r'USRobotics', line):
                continue

            if (m := re.match(r'(.+?)\s+(\d+)\s+(.+?)\s+(\d+)', line)):
                key1, value1, key2, value2 = m.groups()
                if (k1 := self.mapped_name(key1.strip())):
                    data[k1] = int(value1)
                if (k2 := self.mapped_name(key2.strip())):
                    data[k2] = int(value2)
                continue

            if (m := re.match(r'(.+?)\s\s+(\d+)', line)):
                key, value = m.groups()
                if (k := self.mapped_name(key.strip())):
                    data[k] = int(value)
                continue

            if (m := re.match(r'(.+?)\s\s+(.+)', line)):
                key, value = m.groups()
                if (k := self.mapped_name(key.strip())):
                    data[k] = value.strip()
                continue

        # Promote to instance attributes
        for k, v in data.items():
            setattr(self, k, v)
        return data

class ATI6Parser(ATIParserBase):
    def get_mapping(self) -> Dict[str, str]:
        return {
            'Chars sent': 'chars_tx',
            'Chars Received': 'chars_rx',
            'Chars lost': 'chars_lost',
            'Octets sent': 'octets_tx',
            'Octets Received': 'octets_rx',
            'Blocks sent': 'blocks_tx',
            'Blocks Received': 'blocks_rx',
            'Blocks resent': 'blocks_resent',
            'Retrains Requested': 'retr_req',
            'Retrains Granted': 'retr_granted',
            'Line Reversals': 'line_reversals',
            'Blers': 'blers',
            'Link Timeouts': 'link_timeouts',
            'Link Naks': 'link_naks',
            'Data Compression': 'data_compression',
            'Equalization': 'equalization',
            'Fallback': 'fallback',
            'Protocol': 'protocol',
            'Speed': 'speed',
            'Last Call': 'last_call',
            'Disconnect Reason is': 'disconnect_reason'
        }

class ATI11Parser(ATIParserBase):
    def get_mapping(self) -> Dict[str, str]:
        return {
            'Modulation': 'modulation',
            'Carrier Freq ( Hz )': 'carrier_freq',
            'Symbol Rate': 'symbol_rate',
            'Trellis Code': 'trellis_code',
            'Nonlinear Encoding': 'nonlinear_encoding',
            'Precoding': 'precoding',
            'Shaping': 'shaping',
            'Preemphasis Index': 'preemphasis_index',
            'Recv/Xmit Level (-dBm)': 'recv_xmit_level',
            'SNR             ( dB )': 'snr',
            'Near Echo Loss  ( dB )': 'near_echo_loss',
            'Far Echo Loss   ( dB )': 'far_echo_loss',
            'Roundtrip Delay (msec)': 'roundtrip_delay',
            'Round Trip Delay (msec)': 'roundtrip_delay',
            'Timing Offset   ( ppm)': 'timing_offset',
            'Carrier Offset  ( ppm)': 'carrier_offset',
            'RX Upshifts': 'rx_upshifts',
            'RX Downshifts': 'rx_downshifts',
            'TX Speedshifts': 'tx_speedshifts',
            'x2 Status': 'x2_status'
        }

# we tend to use .search because it's not guaranteed the message will be
# at the beginning of the line, maybe fix this in generation
r_notes = re.compile(r'# Notes: (?P<test_notes>.*)')

r_abort = re.compile(
    r'### aborting (?P<ts>\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(,\s|\s-\s)(?P<reason>.*)'
)

r_wc_banner = re.compile(
    r'Connected at (?P<wc_connect_bps>\d+) bps.'
    r'(?P<wc_reliable>Reliable connection.\s+)?'
    r'(?P<wc_ansi_detect>ANSI detected.)?'
)

r_start_qmodem = re.compile(
    r'#### start_qmodem (?:testsize:(?P<testsize>\S+)\sproto:(?P<protocol>\S+)\s)?'
    r'(?P<ts>\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})'
)
r_start_dial = re.compile(
    r'#### start_dial (?P<ts>\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})'
)
r_connected = re.compile(
    r'#### connected (?P<ts>\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})'
)
r_start_download = re.compile(
    r'### start_download (?P<ts>\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})'
)
r_end_download = re.compile(
    r'### end_download (?P<ts>\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})'
)
r_dl_status_cps = re.compile(
    r'\S+\s+-\s+(?P<download_status>SUCCESSFUL!|UNSUCCESSFUL.)'
    r'(?:\s+CPS = (?P<cps>\S+))?'
)
r_stats_ati6 = re.compile(r'### stats_ati6')
r_stats_ati6_end = re.compile(r'### end_stats_ati6')
r_stats_ati11 = re.compile(r'### stats_ati11')
r_stats_ati11_end = re.compile(r'### end_stats_ati11')
r_stats_aty11 = re.compile(r'### stats_aty11')
r_stats_aty11_end = re.compile(r'### end_stats_aty11')
r_stats_atv = re.compile(r'### stats_at&v1')
r_stats_atv_end = re.compile(r'### end_stats_at&v1')

r_end_call = re.compile(
    r'#### end_call (?P<ts>\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})'
)
r_exit_qmodem = re.compile(
    r'#### exit_qmodem (?P<ts>\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})'
)

# Read timestamp from capture files to a datetime object
def ts_obj(ts):
    return datetime.strptime(ts, '%m-%d-%y %H:%M:%S') 

# There shouldn't be any calculations or other logic in read_file, just regex
# matching and storing matches.
def read_file(input_path, args) -> CallSessionStore:
    """
    Takes a log filename, opens it, reads over it line by line extracting
    data with various regular expressions.
    Details of each call are stored in a CallRecord object, and all call
    records are stored in a CallSessionStore object.

    Returns:
        CallSessionStore
    """

    repo = CallSessionStore()

    try:
        file = open(input_path, 'r', encoding='utf-8', errors='replace')
    except FileNotFoundError:
        print(f"The file {file_path} not found.")
    except Exception as e:
        print(f"An error ocurred: {e}")

    with file:
        in_ati = False
        ati_lines = []
        aborted = False

        record = None
        # print("begin read: %s" % CallRecord.count_start_dial)

        # A note on regex matches: often line noise or a broke output can
        # produce spurious garbage causing our expected strings to not start
        # at the beginning of the line. For this reason I often use .search
        # instead of .match

        for line in file:
            # print(line, end="")

            start_qmodem = r_start_qmodem.search(line)
            if start_qmodem:
                ts_start = parse_log_ts(start_qmodem.group('ts'))

                # if we already have a qmodem start time in memory before we
                # begin, that means the previous call aborted, crashed, or
                # otherwise started over. Save what data we have and get ready
                # for the new call
                if record and record.start_qmodem is not None:
                    # print("Detected possible crashed session, saving legacy data from %s" % record.start_qmodem)
                    repo.save(record)

                # start a new empty record and proceed
                record = CallRecord()

                record.connect_type = ConnectionType.DIRECT if args.nullmodem else ConnectionType.MODEM
                record.start_qmodem = ts_start

                # print('Processing new call #%s: %s' % (CallRecord.count_start_dial, record.start_qmodem))

                # Reset aborted flag so we start reading lines again
                aborted = False
                record.start_qmodem = parse_log_ts(start_qmodem.group('ts'))
                continue

            aborted_match = r_abort.search(line)
            if aborted_match:
                record.aborted_time = parse_log_ts(aborted_match.group('ts'))
                record.abort_reason = aborted_match.group('reason')
                # print("  aborted: %s" % record.aborted_time)

                # TODO: if we've aborted, do we want to stop processing remaining lines?
                # need to make sure we capture ATI6 after call?
                aborted = True
                continue

            start_dial = r_start_dial.search(line)
            if start_dial:
                record.start_dial = parse_log_ts(start_dial.group(1))
                continue

            connected = r_connected.search(line)
            if connected:
                record.connect_time = parse_log_ts(connected.group(1))
                record.mark_connected(True)
                continue

            # Qmodem doesn't show us our initial CONNECT speed when using the
            # dial directory, but fortunately Wildcat displays it right
            # before the login prompt.
            bbs_banner = r_wc_banner.search(line)
            if bbs_banner:
                record.remote_connect_bps = int(bbs_banner.group('wc_connect_bps'))
                record.remote_reliable = bbs_banner.group('wc_reliable')
                record.remote_ansi_detected = bbs_banner.group('wc_ansi_detect')
                continue

            start_download = r_start_download.search(line)
            if start_download:
                record.start_download = parse_log_ts(start_download.group(1))
                continue

            # Watching for the 'SUCCESSFUL!  CPS = xxxxx' or 'UNSUCCESSFUL.'
            # message is important, we will emit this end_download line whether we
            # succeed for fail. It should come after we see end_download.
            # The CPS is only calculated on successful downloads.

            end_download = r_end_download.search(line)
            if end_download:
                record.end_download = parse_log_ts(end_download.group(1))
                continue

            download_cps = r_dl_status_cps.search(line)
            if download_cps:
                download_result = download_cps.group(1)
                if download_result == 'SUCCESSFUL!':
                    record.mark_download_success(True)
                else:
                    record.mark_download_success(False)
  
                cps = download_cps.group(2)
                if cps:
                    record.download_cps = int(cps.replace(",", ""))
                continue

            end_call = r_end_call.match(line)
            if end_call:
                record.end_call = parse_log_ts(end_call.group(1))
                continue


            # Save all lines from ati6/ati11 commands to parse them in a
            # dedicated function.
            # If we've aborted we may not be able to communicate with the mdoem
            begin_stats_ati6 = r_stats_ati6.match(line)
            if begin_stats_ati6:
                # print('  -- starting ati6 gather')
                in_ati = True
                ati_lines = []
                continue

            if in_ati:
                ati_lines.append(line.strip())
                # don't 'continue' here, keep processing lines to see when we've
                # finished reading in stats

            end_stats_ati6 = r_stats_ati6_end.match(line)
            if end_stats_ati6:
                # flush current ati lines
                in_ati = False
                record.ati6 = ATI6Parser(ati_lines)
                ati_lines = []
                continue

            begin_stats_ati11 = r_stats_ati11.match(line)
            if begin_stats_ati11:
                # print('  -- starting ati11 gather')
                in_ati = True
                ati_lines = []
              
            end_stats_ati11 = r_stats_ati11_end.match(line)
            if end_stats_ati11:
                # flush current ati lines
                in_ati = False
                record.ati11 = ATI11Parser(ati_lines)
                ati_lines = []
                continue

            exit_qmodem = r_exit_qmodem.match(line)
            if exit_qmodem:
                record.exit_qmodem = parse_log_ts(exit_qmodem.group(1))
                repo.save(record)

                # free the stats variable for the next call
                # record = CallRecord()
                record = None
                # print("  end of: %s" % CallRecord.count_start_dial)
                # print()
                continue

        # --finished reading lines here--

    # Save if we have an unsaved last/aborted call
    if record is not None:
        repo.save(record)

    # debug to make sure we saw everything
    print(
        f"Connect attempt / success / failures:    "
        f"{CallRecord.count_start_dial} / {CallRecord.count_connected} / "
        f"{CallRecord.connect_failure_count()}, success: {CallRecord.connect_success_percent():.2f}%"
    )

    return repo

def print_report2(sessions, args):

    def fmt_optional(val: Optional[str], width=1) -> str:
        """
        Return a formatted text string with the contents of val,
        if val is None, just return a '-' text string to indicate no data.
        width specifies the width of the formatted string, default to 1.
        """
        return f"{val:{width}}" if val is not None else f"{'-':>{width}}"

    count_calls = 0
    # count_download_success = 0
    # count_download_failure = 0
    # count_download_failure
    list_connect = []
    list_connect_time = []

    for rec in sessions.all():
        # pprint(rec)
        
        count_calls += 1

        # build summary statistics
        if rec.remote_connect_bps is not None:
            list_connect.append(rec.remote_connect_bps)

        if rec.connect_type == ConnectionType.DIRECTSERIAL:
            list_connect_time.append(0)
        else:
            list_connect_time.append(rec.handshake_duration())

        # if rec.download_success:
        #     count_download_success += 1

        ati6 = rec.ati6.get_data() if rec.ati6 else {}
        ati11 = rec.ati11.get_data() if rec.ati11 else {}

        print(
            f"{rec.start_qmodem.strftime('%Y-%m-%d %H:%M:%S')}: "
            f"{fmt_optional(rec.remote_connect_bps, 6)}   "
            f"{rec.handshake_duration():3d}   "
            f"{fmt_optional(rec.download_success_msg(), 10)}  "
            f"{fmt_optional(rec.download_cps, 6)}  "
            f"{fmt_optional(rec.download_duration(), 4)}  "
            f"{fmt_optional(ati6.get('speed', '-'), 5)}  "
            f"{fmt_optional(ati6.get('protocol', '-'), 16)}  {ati11.get('roundtrip_delay', '-')}  "
            f"{fmt_optional(rec.call_duration(), 4)}  "
            f"{ati6.get('retr_req', '-')}/{ati6.get('retr_granted','-')}  {ati6.get('blers','-')} "
            f"{rec.termination_reason()} "
        )

    sessions.report_aggregates()
    # sessions.test()

def main():
    parser = argparse.ArgumentParser(
        description="Parse a Qmodem capture log and generate a summary of modem calls"
    )
    parser.add_argument('file_path', type=str, help='Path to the Qmodem capture file')
    parser.add_argument(
        '--nullmodem',
        action='store_true',
        help='Indictes that the connection is a null modem (direct serial) connection, not a modem dial-up connection'
    )
    args = parser.parse_args()

    sessions = CallSessionStore()

    sessions = read_file(args.file_path, args)
    # pprint(sessions.all())
    print_report2(sessions, args)

if __name__ == "__main__":
    main()
