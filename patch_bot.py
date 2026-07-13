import os
import glob

def patch():
    files = glob.glob('src/**/*.py', recursive=True)
    for f in files:
        with open(f, 'r', encoding='utf-8') as file:
            content = file.read()
        
        orig = content
        content = content.replace("AsyncClient(self.token)", "AsyncClient(self.token, use_default_enum_if_error=True)")
        content = content.replace("AsyncClient(token)", "AsyncClient(token, use_default_enum_if_error=True)")
        
        if content != orig:
            with open(f, 'w', encoding='utf-8') as file:
                file.write(content)
            print(f"Patched {f}")

if __name__ == "__main__":
    patch()
