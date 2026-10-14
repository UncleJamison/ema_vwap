import py_compile

for f in ["src/providers/crypto.py", "src/wfo.py", "src/app.py"]:
    try:
        py_compile.compile(f, doraise=True)
        print(f"OK {f}")
    except Exception as e:
        print(f"FAIL {f}: {e}")
