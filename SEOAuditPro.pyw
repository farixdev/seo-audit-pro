# -*- coding: utf-8 -*-
"""Windows launcher for SEO Audit Pro (no console window).

``SEOAuditPro.exe`` opens the desktop app. The app starts the same program with
``--serve`` to run its built-in API server in "This computer" mode, and with
``--job <folder>`` for heavy work in the background (an audit, a report file).
"""

import sys

if __name__ == "__main__":
    if "--job" in sys.argv[1:]:
        from seo_audit_pro.pc_jobs import main as job
        job(sys.argv[1:])
    elif "--diagnose" in sys.argv[1:]:   # check this computer, write a short report
        from seo_audit_pro.app import diagnose
        diagnose(sys.argv[1:])
    elif "--selftest" in sys.argv[1:]:   # run one Scrape & Audit audit in the hidden window, log the result
        from seo_audit_pro.app import selftest
        selftest(sys.argv[1:])
    elif "--serve" in sys.argv[1:]:
        from seo_audit_pro.local_server import serve
        serve(sys.argv[1:])
    else:
        from seo_audit_pro.app import main
        main()
