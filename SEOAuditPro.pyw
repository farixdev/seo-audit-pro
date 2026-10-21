# -*- coding: utf-8 -*-
"""Windows launcher for SEO Audit Pro (no console window).

``SEOAuditPro.exe`` opens the desktop app. The app starts the same program with
``--serve`` to run its built-in API server in "This computer" mode.
"""

import sys

if __name__ == "__main__":
    if "--serve" in sys.argv[1:]:
        from seo_audit_pro.local_server import serve
        serve(sys.argv[1:])
    else:
        from seo_audit_pro.app import main
        main()
