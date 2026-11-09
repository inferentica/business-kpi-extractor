"""The archived reports read for years EDGAR holds only as paper (foreign filers before May 2002): each one's address
and the Internet Archive capture it is read from, the period it reports and the date it was filed or published.

Found through the Wayback Machine's index of the companies' own sites (tsmc.com.tw, investor.tsmc.com, asml.com,
asml.nl). A filing date the document states is used ("As filed with the Securities and Exchange Commission on May 9
2002"); a quarterly release's is taken as a month after its quarter, which only orders it among other presentations.
"""
from __future__ import annotations

from datetime import date, timedelta

from .archives import Archived


def _A(symbol, form, year, month, day, filed, url, timestamp) -> Archived:
    return Archived(symbol, form, date(year, month, day), filed, url, timestamp)


def _Q(symbol, year, month, day, url, timestamp) -> Archived:
    end = date(year, month, day)
    return Archived(symbol, "6-K", end, end + timedelta(days=30), url, timestamp)


ARCHIVED = (
    # TSMC's 20-Fs filed on paper, consolidated (FY2000: 1998-2000; FY2001: 1999-2001).
    _A("TSM", "20-F", 2000, 12, 31, date(2001, 6, 21), "http://www.tsmc.com.tw:80/download/C0202/20F-2000.pdf", "20030516074130"),
    _A("TSM", "20-F", 2001, 12, 31, date(2002, 5, 9), "http://www.tsmc.com.tw:80/download/C0202/20F-2001.pdf", "20030516124740"),
    # ASML's 20-F for 2001 filed on paper, and its 2000 annual report in English (before the SVG merger's restatement).
    _A("ASML", "20-F", 2001, 12, 31, date(2002, 5, 7), "http://www.asml.nl:80/doclib/investor/20_f_2001_filing.pdf",
       "20040620115533"),
    _A("ASML", "ARS", 2000, 12, 31, date(2001, 3, 15),
       "http://www.asml.com:80/investors/pdf/annual_report2000definitief_eng.pdf", "20010611214813"),
    # TSMC's quarterly statements, the company alone (unconsolidated), 1998 to the second quarter of 2002.
    _Q("TSM", 1998, 3, 31, "http://www.tsmc.com.tw:80/download/c020207/1q98_cf.pdf", "20040508110700"),  # 1q98_cf.pdf
    _Q("TSM", 1998, 6, 30, "http://www.tsmc.com.tw:80/download/c020207a/2q98_is.pdf", "20040508124639"),  # 2q98_is.pdf
    _Q("TSM", 1998, 9, 30, "http://www.tsmc.com.tw:80/download/c020207b/3q98_cf.pdf", "20040316172439"),  # 3q98_cf.pdf
    _Q("TSM", 1998, 9, 30, "http://www.tsmc.com.tw:80/download/c020207b/3q98_ytd.pdf", "20040316173050"),  # 3q98_ytd.pdf
    _Q("TSM", 1998, 12, 31, "http://www.tsmc.com.tw:80/download/c020207c/4q98_bs.pdf", "20040506052824"),  # 4q98_bs.pdf
    _Q("TSM", 1998, 12, 31, "http://www.tsmc.com.tw:80/download/c020207c/4q98_cf.pdf", "20040506052511"),  # 4q98_cf.pdf
    _Q("TSM", 1998, 12, 31, "http://www.tsmc.com.tw:80/download/c020207c/4q98_is.pdf", "20040506071711"),  # 4q98_is.pdf
    _Q("TSM", 1998, 12, 31, "http://www.tsmc.com.tw:80/download/c020207c/4q98_ytd.pdf", "20040506044004"),  # 4q98_ytd.pdf
    _Q("TSM", 1999, 3, 31, "http://www.tsmc.com.tw:80/download/c020208/1q99_bs.pdf", "20040506032957"),  # 1q99_bs.pdf
    _Q("TSM", 1999, 3, 31, "http://www.tsmc.com.tw:80/download/c020208/1q99_cf.pdf", "20040506050545"),  # 1q99_cf.pdf
    _Q("TSM", 1999, 3, 31, "http://www.tsmc.com.tw:80/download/c020208/1q99_is.pdf", "20040506050814"),  # 1q99_is.pdf
    _Q("TSM", 1999, 6, 30, "http://www.tsmc.com.tw:80/download/c020208a/2q99_bs.pdf", "20040506053234"),  # 2q99_bs.pdf
    _Q("TSM", 1999, 6, 30, "http://www.tsmc.com.tw:80/download/c020208a/2q99_cf.pdf", "20040506053424"),  # 2q99_cf.pdf
    _Q("TSM", 1999, 6, 30, "http://www.tsmc.com.tw:80/download/c020208a/2q99_is.pdf", "20040313234626"),  # 2q99_is.pdf
    _Q("TSM", 1999, 6, 30, "http://www.tsmc.com.tw:80/download/c020208a/2q99_ytd.pdf", "20040506041356"),  # 2q99_ytd.pdf
    _Q("TSM", 1999, 9, 30, "http://www.tsmc.com.tw:80/download/c020208b/3q99_bs.pdf", "20040508132135"),  # 3q99_bs.pdf
    _Q("TSM", 1999, 12, 31, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/1999/Q4/4q99_bs.pdf", "20210513095628"),  # 4q99_bs.pdf
    _Q("TSM", 1999, 12, 31, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/1999/Q4/4q99_cf.pdf", "20210511140859"),  # 4q99_cf.pdf
    _Q("TSM", 1999, 12, 31, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/1999/Q4/4q99_is.pdf", "20210513085944"),  # 4q99_is.pdf
    _Q("TSM", 1999, 12, 31, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/1999/Q4/4q99fr.pdf", "20210511132959"),  # 4q99fr.pdf
    _Q("TSM", 2000, 3, 31, "http://www.tsmc.com.tw:80/uploadfile/ir/quarterly/2000/1/E/1q00_bs.pdf", "20030517105519"),  # 1q00_bs.pdf
    _Q("TSM", 2000, 3, 31, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2000/Q1/1q00_cf.pdf", "20220121235515"),  # 1q00_cf.pdf
    _Q("TSM", 2000, 3, 31, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2000/Q1/1q00_is.pdf", "20220121230230"),  # 1q00_is.pdf
    _Q("TSM", 2000, 6, 30, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2000/Q2/2q00_cf_post.pdf", "20220121232220"),  # 2q00_cf_post.pdf
    _Q("TSM", 2000, 6, 30, "http://www.tsmc.com.tw:80/uploadfile/ir/quarterly/2000/2/E/2q00_is_pre.pdf", "20030517121849"),  # 2q00_is_pre.pdf
    _Q("TSM", 2000, 6, 30, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2000/Q2/2q00fr.pdf", "20220121235436"),  # 2q00fr.pdf
    _Q("TSM", 2000, 9, 30, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2000/Q3/3q00fr.pdf", "20220121222913"),  # 3q00fr.pdf
    _Q("TSM", 2000, 9, 30, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2000/Q3/3q00is.pdf", "20220121225844"),  # 3q00is.pdf
    _Q("TSM", 2000, 12, 31, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2000/Q4/4Q00EarningsRelease.pdf", "20210511124019"),  # 4Q00EarningsRelease.pdf
    _Q("TSM", 2000, 12, 31, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2000/Q4/4Q00FS.pdf", "20210511132649"),  # 4Q00FS.pdf
    _Q("TSM", 2000, 12, 31, "http://www.tsmc.com.tw:80/uploadfile/ir/quarterly/2000/4/E/4q00bs.pdf", "20030516221234"),  # 4q00bs.pdf
    _Q("TSM", 2000, 12, 31, "http://www.tsmc.com.tw:80/uploadfile/ir/quarterly/2000/4/E/4q00cf.pdf", "20030517041648"),  # 4q00cf.pdf
    _Q("TSM", 2000, 12, 31, "http://www.tsmc.com.tw:80/uploadfile/ir/quarterly/2000/4/E/4q00is.pdf", "20030517130429"),  # 4q00is.pdf
    _Q("TSM", 2001, 3, 31, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2001/Q1/1Q01_bs-3.pdf", "20220122001843"),  # 1Q01_bs-3.pdf
    _Q("TSM", 2001, 3, 31, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2001/Q1/1Q01_cf.pdf", "20220123213551"),  # 1Q01_cf.pdf
    _Q("TSM", 2001, 3, 31, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2001/Q1/1Q01_fr.pdf", "20220122000909"),  # 1Q01_fr.pdf
    _Q("TSM", 2001, 6, 30, "http://www.tsmc.com.tw:80/uploadfile/ir/quarterly/2001/2/E/2Q01IS.pdf", "20040313222829"),  # 2Q01IS.pdf
    _Q("TSM", 2001, 6, 30, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2001/Q2/2q01_cf.pdf", "20220122000028"),  # 2q01_cf.pdf
    _Q("TSM", 2001, 9, 30, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2001/Q3/3q01_bs.pdf", "20220121160821"),  # 3q01_bs.pdf
    _Q("TSM", 2001, 9, 30, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2001/Q3/3q01_cf.pdf", "20220121223606"),  # 3q01_cf.pdf
    _Q("TSM", 2001, 9, 30, "http://www.tsmc.com.tw:80/uploadfile/ir/quarterly/2001/3/E/3q01_is.pdf", "20030517122121"),  # 3q01_is.pdf
    _Q("TSM", 2001, 9, 30, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2001/Q3/3q01fr.pdf", "20220121235942"),  # 3q01fr.pdf
    _Q("TSM", 2001, 9, 30, "http://www.tsmc.com.tw:80/uploadfile/ir/quarterly/2001/3/E/3q01is_ytd.pdf", "20030727030937"),  # 3q01is_ytd.pdf
    _Q("TSM", 2001, 12, 31, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2001/Q4/4q01BS.pdf", "20210511125758"),  # 4q01BS.pdf
    _Q("TSM", 2001, 12, 31, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2001/Q4/4q01CF.pdf", "20210511142243"),  # 4q01CF.pdf
    _Q("TSM", 2001, 12, 31, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2001/Q4/4q01IS.pdf", "20210511133631"),  # 4q01IS.pdf
    _Q("TSM", 2001, 12, 31, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2001/Q4/4q01IS_YTD.pdf", "20210513095437"),  # 4q01IS_YTD.pdf
    _Q("TSM", 2002, 3, 31, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2002/Q1/fs1q02.pdf", "20220123210639"),  # fs1q02.pdf
    _Q("TSM", 2002, 3, 31, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2002/Q1/pm1q02.pdf", "20220121223853"),  # pm1q02.pdf
    _Q("TSM", 2002, 6, 30, "http://www.tsmc.com.tw:80/uploadfile/ir/quarterly/2002/2/E/2Q02-FR.pdf", "20030517150707"),  # 2Q02-FR.pdf
    _Q("TSM", 2002, 6, 30, "https://investor.tsmc.com/english/encrypt/files/encrypt_file/english/2002/Q2/2Q02FS.pdf", "20220122003514"),  # 2Q02FS.pdf
)
