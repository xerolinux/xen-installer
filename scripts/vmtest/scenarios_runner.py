import importlib
import json
import sys
import traceback

from fixtures import sh

STATE = "/root/xen/state.json"


class Ctx:
    sh = staticmethod(sh)

    @staticmethod
    def disk(i):
        return f"/dev/vd{chr(ord('a') + i)}"


def main(name, phase):
    status, detail, state = "PASS", "", None
    try:
        module = importlib.import_module(f"scenarios.{name}")
        if phase == "prepare":
            state = module.prepare(Ctx())
        elif phase == "verify":
            with open(STATE) as fh:
                module.verify(Ctx(), json.load(fh))
        else:
            state = module.run(Ctx())
    except Exception as exc:
        traceback.print_exc()
        status = "FAIL"
        detail = "".join(traceback.format_exception_only(exc)).strip()
    result = {"scenario": name, "status": status, "detail": detail, "state": state}
    print("XEN-RESULT " + json.dumps(result), flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
