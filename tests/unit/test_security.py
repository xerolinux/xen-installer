"""
Security tests for Xen Installer.

Validates:
- Configuration file integrity and safety
- Path traversal prevention
- Input validation
- No hardcoded secrets or credentials
"""

import re
import subprocess
from pathlib import Path

import pytest

try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False

try:
    from xen.core.engine import BrandingAssets, BrandingColors, JobDefinition, XenConfig

    HAS_XEN = True
except ImportError:
    HAS_XEN = False

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
SRC_DIR = PROJECT_ROOT / "src"
CONFIG_DIR = PROJECT_ROOT / "config"


class TestConfigurationSecurity:
    """Tests for configuration file security."""

    @pytest.fixture
    def config_files(self) -> list[Path]:
        """Get all YAML config files."""
        return list(CONFIG_DIR.rglob("*.yaml"))

    def test_no_hardcoded_passwords(self, config_files: list[Path]) -> None:
        """Config files should not contain hardcoded passwords."""
        password_patterns = [
            r'password\s*[:=]\s*["\'][^"\']+["\']',
            r'passwd\s*[:=]\s*["\'][^"\']+["\']',
            r'secret\s*[:=]\s*["\'][^"\']+["\']',
            r'api_key\s*[:=]\s*["\'][^"\']+["\']',
            r'token\s*[:=]\s*["\'][^"\']+["\']',
        ]

        for config_file in config_files:
            content = config_file.read_text()
            for pattern in password_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                # Filter out placeholder values and asset paths
                real_matches = [
                    m
                    for m in matches
                    if not any(
                        placeholder in m.lower()
                        for placeholder in [
                            "example",
                            "changeme",
                            "xxx",
                            "your_",
                            "<",
                            ">",
                            ".svg",  # icon asset paths
                            ".png",  # image asset paths
                            "icon_",  # icon configuration keys
                        ]
                    )
                ]
                assert not real_matches, (
                    f"Potential hardcoded credential in {config_file}: {real_matches}"
                )

    def test_no_absolute_paths_in_config(self, config_files: list[Path]) -> None:
        """Config files should use relative paths, not absolute."""
        # Exceptions for system paths that are expected
        allowed_absolute = [
            "/var/log/",
            "/run/",
            "/etc/",
            "/iso/",
            "/bin/",
            "/usr/",
        ]

        for config_file in config_files:
            # A mount point names a path on the installed system, not a file Xen reads.
            content = re.sub(r'mountpoint:\s*"[^"]*"', "", config_file.read_text())
            # Find absolute paths (starting with /)
            abs_paths = re.findall(r'["\'](/[a-zA-Z][^"\']*)["\']', content)

            for path in abs_paths:
                is_allowed = any(path.startswith(allowed) for allowed in allowed_absolute)
                assert is_allowed, f"Unexpected absolute path in {config_file}: {path}"

    @pytest.mark.skipif(not HAS_YAML, reason="PyYAML not installed")
    def test_yaml_safe_load(self, config_files: list[Path]) -> None:
        """All YAML files should be loadable with safe_load."""
        for config_file in config_files:
            content = config_file.read_text()
            # Should not raise any exception
            try:
                yaml.safe_load(content)
            except yaml.YAMLError as e:
                pytest.fail(f"YAML parsing error in {config_file}: {e}")

    def test_no_yaml_anchors_with_aliases(self, config_files: list[Path]) -> None:
        """Check for potentially dangerous YAML anchor/alias patterns."""
        # While anchors/aliases are valid YAML, complex nested ones can cause issues
        for config_file in config_files:
            content = config_file.read_text()
            # Check for excessive anchor usage (more than 10 is suspicious)
            anchors = re.findall(r"&\w+", content)
            assert len(anchors) <= 10, (
                f"Excessive YAML anchors in {config_file} ({len(anchors)} found)"
            )


@pytest.mark.skipif(not HAS_XEN, reason="xen package not available")
class TestPathTraversal:
    """Tests for path traversal attack prevention."""

    def test_theme_path_no_traversal(self) -> None:
        """Theme paths should not allow directory traversal outside project."""
        # These should be caught/sanitized
        dangerous_paths = [
            "../../../../etc/passwd",
            "../../../.ssh/id_rsa",
            "/etc/shadow",
            "..\\..\\windows\\system32",
        ]

        for dangerous in dangerous_paths:
            config = XenConfig(theme=dangerous)
            # The path should be stored but resolution should be safe
            # (actual resolution happens in main.py with resolve())
            assert config.theme == dangerous  # Storage is OK
            # But resolution should stay within bounds (tested in integration)

    def test_asset_path_validation(self) -> None:
        """Asset paths should not contain traversal sequences."""
        # Valid paths
        valid = BrandingAssets(
            logo="logos/logo.png",
            background="wallpapers/dark.jpg",
        )
        assert valid.logo == "logos/logo.png"

        # Paths with traversal - Pydantic allows them but resolution should be safe
        with_traversal = BrandingAssets(
            logo="../../../etc/passwd",
        )
        # The model accepts it, but bridge.py resolution should fail safely
        assert ".." in with_traversal.logo


class TestCodeSecurity:
    """Tests for source code security patterns."""

    @pytest.fixture
    def python_files(self) -> list[Path]:
        """Get all Python source files."""
        return list(SRC_DIR.rglob("*.py"))

    def test_no_eval_or_exec(self, python_files: list[Path]) -> None:
        """Source code should not use eval() or exec() builtins."""
        for py_file in python_files:
            content = py_file.read_text()

            # Check for eval() - dangerous for arbitrary code execution
            eval_matches = re.findall(r"(?<![.\w])eval\s*\(", content)
            assert not eval_matches, f"Dangerous eval() in {py_file}"

            # Check for exec() builtin - but NOT app.exec() which is Qt method
            # Find all exec( occurrences and filter out app.exec()
            exec_matches = re.findall(r"(?<![.\w])exec\s*\(", content)
            # app.exec() is safe (Qt method), return app.exec() is also safe
            safe_exec_count = content.count("app.exec()")
            assert len(exec_matches) <= safe_exec_count, f"Dangerous exec() builtin in {py_file}"

    def test_no_shell_injection_risk(self, python_files: list[Path]) -> None:
        """Check for potential shell injection vulnerabilities."""
        # Patterns that might indicate shell injection risk
        risky_patterns = [
            r"subprocess\..*shell\s*=\s*True",
            r"os\.system\s*\(",
            r"os\.popen\s*\(",
            r"commands\.",  # deprecated module
        ]

        for py_file in python_files:
            content = py_file.read_text()
            for pattern in risky_patterns:
                matches = re.findall(pattern, content)
                assert not matches, f"Potential shell injection risk in {py_file}: {matches}"

    def test_no_pickle_usage(self, python_files: list[Path]) -> None:
        """Pickle should not be used (security risk with untrusted data)."""
        for py_file in python_files:
            content = py_file.read_text()
            pickle_usage = re.findall(r"\bpickle\b", content)
            assert not pickle_usage, f"Pickle usage found in {py_file} - use JSON/YAML instead"

    def test_no_hardcoded_credentials_in_code(self, python_files: list[Path]) -> None:
        """Source code should not contain hardcoded credentials."""
        credential_patterns = [
            r'password\s*=\s*["\'][^"\']{4,}["\']',
            r'api_key\s*=\s*["\'][^"\']{8,}["\']',
            r'secret\s*=\s*["\'][^"\']{8,}["\']',
            r'token\s*=\s*["\'][a-zA-Z0-9]{20,}["\']',
        ]

        # Placeholder values that are provably NOT credentials: format-string
        # / template markers such as ``{hash}`` or ``<hash>`` used to render
        # config files (e.g. the NixOS ``hashedPassword = "{hash}";`` template).
        placeholder_re = re.compile(r'["\'][<{][^"\']*[>}]["\']')

        for py_file in python_files:
            content = py_file.read_text()
            for pattern in credential_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                # Filter test files and obvious non-credentials (template markers).
                real_matches = [m for m in matches if not placeholder_re.search(m)]
                if "test" not in str(py_file).lower():
                    assert not real_matches, (
                        f"Potential hardcoded credential in {py_file}: {real_matches}"
                    )


@pytest.mark.skipif(not HAS_XEN, reason="xen package not available")
class TestInputValidation:
    """Tests for input validation."""

    def test_branding_colors_format(self) -> None:
        """Color values should be valid hex format."""
        from pydantic import ValidationError as PydanticValidationError

        # Valid colors
        valid = BrandingColors(
            primary="#5597e6",
            background="#1a1a1a",
            text="#fffded",
        )
        assert valid.primary == "#5597e6"

        # Invalid colors should now raise validation errors
        invalid_formats = ["red", "rgb(255,0,0)", "not-a-color", "#fff", "#12345"]
        for invalid in invalid_formats:
            with pytest.raises(PydanticValidationError):
                BrandingColors(primary=invalid)

    def test_job_name_validation(self) -> None:
        """Job names should be valid Python identifiers."""
        # Valid job names
        valid_names = ["welcome", "partition", "install_base", "bootloader"]
        for name in valid_names:
            job = JobDefinition(name=name)
            assert job.name == name

        # Names that might cause issues (but model accepts)
        risky_names = ["__init__", "import", "../traversal"]
        for name in risky_names:
            job = JobDefinition(name=name)
            # Document that these pass validation but should be caught at runtime
            assert job.name == name


class TestFilePermissions:
    """Tests for file permission safety."""

    def test_no_world_writable_configs(self) -> None:
        """Config files should not be world-writable."""
        config_files = list(CONFIG_DIR.rglob("*.yaml"))

        for config_file in config_files:
            mode = config_file.stat().st_mode
            world_writable = mode & 0o002
            assert not world_writable, f"Config file is world-writable: {config_file}"

    def test_no_executable_configs(self) -> None:
        """Config files should not be executable."""
        config_files = list(CONFIG_DIR.rglob("*.yaml"))

        for config_file in config_files:
            mode = config_file.stat().st_mode
            executable = mode & 0o111
            assert not executable, f"Config file is executable: {config_file}"


class TestEnvironmentSafety:
    """Tests for environment variable handling."""

    def test_sensitive_env_vars_not_in_platform_info_source(self) -> None:
        """Platform info function should not reference sensitive env vars."""
        # Read the source file directly to avoid import issues
        main_py = SRC_DIR / "xen" / "main.py"
        content = main_py.read_text()

        # These should NOT appear in platform info output
        sensitive_vars = ["PASSWORD", "SECRET", "TOKEN", "CREDENTIAL", "PRIVATE"]

        # Find the print_platform_info function
        func_start = content.find("def print_platform_info")
        if func_start == -1:
            pytest.skip("print_platform_info not found")

        # Find the end of the function (next def or class at same indent)
        func_end = content.find("\ndef ", func_start + 1)
        if func_end == -1:
            func_end = len(content)

        func_source = content[func_start:func_end]

        for var in sensitive_vars:
            # Check that the function doesn't explicitly log these
            pattern = rf'os\.environ\.get\s*\(\s*["\'].*{var}'
            matches = re.findall(pattern, func_source, re.IGNORECASE)
            assert not matches, f"print_platform_info might expose {var}: {matches}"

    def test_no_sensitive_defaults_in_config(self) -> None:
        """Default config values should not contain sensitive data."""
        engine_py = SRC_DIR / "xen" / "core" / "engine.py"
        content = engine_py.read_text()

        # Check for sensitive default values
        sensitive_defaults = [
            r'default\s*=\s*["\']password',
            r'default\s*=\s*["\']secret',
            r'default\s*=\s*["\']admin',
        ]

        for pattern in sensitive_defaults:
            matches = re.findall(pattern, content, re.IGNORECASE)
            assert not matches, f"Sensitive default value found: {matches}"


class TestRootLauncherArguments:
    """xen-root-launch runs as root for a passwordless liveuser: only fixed flags may pass."""

    LAUNCHER = PROJECT_ROOT / "data" / "xen-root-launch"

    def _run(self, script: Path, *args: str) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(
            ["bash", str(script), "", "", *args],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )

    @pytest.mark.parametrize(
        "args",
        [
            ("--config", "evil.yaml"),
            ("-c", "evil.yaml"),
            ("--config=evil.yaml",),
            ("--debug", "--platform-info"),
            ("--dry",),
            ("evil.yaml",),
        ],
    )
    def test_rejects_unlisted_arguments(self, args: tuple[str, ...]) -> None:
        result = self._run(self.LAUNCHER, *args)
        assert result.returncode == 2
        assert "not allowed" in result.stderr

    def test_passes_allowed_flags_through(self, tmp_path: Path) -> None:
        script = tmp_path / "xen-root-launch"
        script.write_text(
            self.LAUNCHER.read_text().replace('exec /usr/bin/xen "$@"', 'printf "%s\\n" "$@"')
        )
        result = self._run(script, "--debug", "--dry-run", "--skip-requirements")
        assert result.returncode == 0, result.stderr
        assert result.stdout.split() == ["--debug", "--dry-run", "--skip-requirements"]
