#!/usr/bin/env python3
"""2026-10-18: put file.zhizinan.top (PairDrop on Phoenix) on EdgeOne zone zhizinan.top, same shape as s2a / gpt:
origin 132.226.73.207 over HTTPS 443, Host = itself, EO free certificate, DNSPod CNAME -> <name>.eo.dnse1.com.
Runs on the Mac with .secrets/tencentcloud.key. Every step checks the current state first, so re-running is safe.

    python3 scripts/pairdrop/eo_setup.py

Rollback: delete the DNSPod records file / _dnsauth.file, then the EO acceleration domain (ModifyAccelerationDomainStatuses
offline + DeleteAccelerationDomains).
"""
import os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
_p = os.path.join(HERE, "..", "cutover", "switch_origins.py")
_g = {"__file__": _p}
exec(open(_p).read().split("args = sys.argv")[0], _g)   # reuse call() / ZONE_ID / NEW without running the cutover script
call, ZONE_ID, ORIGIN, DOMAIN = _g["call"], _g["ZONE_ID"], _g["NEW"], _g["DOMAIN"]
teo = lambda action, payload: call("teo", action, "2022-09-01", payload)
dns = lambda action, payload: call("dnspod", action, "2021-03-23", dict(payload, Domain=DOMAIN))

SUB = "file"
HOST = SUB + "." + DOMAIN


def eo_domain():
    ds = teo("DescribeAccelerationDomains", {"ZoneId": ZONE_ID, "Filters": [{"Name": "domain-name", "Values": [HOST]}]})
    return next((d for d in ds["AccelerationDomains"] if d["DomainName"] == HOST), None)


def ensure_record(name, rtype, value):
    try:
        recs = dns("DescribeRecordList", {"Subdomain": name, "Limit": 100})["RecordList"]
    except SystemExit as e:   # DNSPod answers "no records" with an error
        if "NoDataOfRecord" not in str(e): raise
        recs = []
    recs = [r for r in recs if r["Name"] == name and r["Type"] == rtype]
    if any(r["Value"].rstrip(".") == value.rstrip(".") for r in recs):
        print("dns   %s %s already %s" % (name, rtype, value)); return
    if recs:
        raise SystemExit("dns %s %s exists with another value: %s" % (name, rtype, [r["Value"] for r in recs]))
    dns("CreateRecord", {"SubDomain": name, "RecordType": rtype, "RecordLine": "默认", "Value": value, "TTL": 600})
    print("dns   %s %s -> %s (created)" % (name, rtype, value))


d = eo_domain()
if not d:
    teo("CreateAccelerationDomain", {"ZoneId": ZONE_ID, "DomainName": HOST,
                                     "OriginInfo": {"OriginType": "IP_DOMAIN", "Origin": ORIGIN, "HostHeader": HOST},
                                     "OriginProtocol": "HTTPS", "HttpOriginPort": 80, "HttpsOriginPort": 443, "IPv6Status": "follow"})
    print("eo    %s created" % HOST)
    d = eo_domain()
print("eo    %s status=%s cname=%s origin=%s %s host=%s" % (HOST, d["DomainStatus"], d["Cname"], d["OriginDetail"]["Origin"],
      d["OriginProtocol"], d["OriginDetail"].get("HostHeader")))

ensure_record(SUB, "CNAME", d["Cname"])

cert = d.get("Certificate") or {}
if cert.get("Mode") == "eofreecert" and any(c.get("Status") == "deployed" for c in cert.get("List") or []):
    print("cert  eofreecert already deployed"); sys.exit(0)
# This zone only allows "auto verification": ApplyFreeCertificate is refused (CurrentZoneOnlySupportAutoVerification),
# binding eofreecert starts issuance, which EO verifies through the CNAME above.
if cert.get("Mode") != "eofreecert":
    teo("ModifyHostsCertificate", {"ZoneId": ZONE_ID, "Hosts": [HOST], "Mode": "eofreecert"})
    print("cert  mode set to eofreecert")
for i in range(40):
    c = eo_domain().get("Certificate") or {}
    st = [(x.get("Status"), x.get("ExpireTime")) for x in c.get("List") or []]
    print("cert  %s %s" % (c.get("Mode"), st or "(not issued yet)"))
    if any(s == "deployed" for s, _ in st): break
    time.sleep(15)
else:
    raise SystemExit("free certificate not deployed yet; re-run later")
