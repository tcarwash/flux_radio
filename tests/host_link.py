"""Native linker flags for host fixtures; ARM ELF builds use their own toolchain."""
import sys


def gc_sections():
    return '-Wl,-dead_strip' if sys.platform == 'darwin' else '-Wl,--gc-sections'


def exported_symbols(path, patterns):
    if sys.platform == 'darwin':
        path.write_text(''.join(f'_{name}\n' for name in patterns))
        return '-Wl,-exported_symbols_list,' + str(path)
    path.write_text('{ global: ' + '; '.join(patterns) + '; local: *; };')
    return '-Wl,--version-script=' + str(path)
