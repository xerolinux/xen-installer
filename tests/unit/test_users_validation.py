"""
Unit tests for UsersView validation logic.

PORTÉE : ce module teste UNIQUEMENT les motifs de validation côté UI/QML
(reproduits ici en Python pour vérifier les regex utilisées dans UsersView.qml).
Il NE teste PAS le job Python `UsersJob` : la validation, le run() et la sécurité
du mot de passe du job sont couverts par tests/unit/test_users_job.py.

Tests the validation patterns used in QML/JavaScript for:
- Username (POSIX compliant: lowercase letters, numbers, hyphen, underscore)
- Hostname (RFC 1123: lowercase letters, numbers, hyphen)
- Password (NIST SP 800-63B inspired criteria)
"""

import re

import pytest


class TestUsernameValidation:
    """
    Tests for username validation pattern.

    POSIX username requirements:
    - Must start with a lowercase letter OR an underscore
    - Can contain lowercase letters, digits, hyphens, and underscores
    - Typically max 32 characters (Linux default)
    """

    # Pattern from UsersView.qml (aligné sur UsersJob.USERNAME_PATTERN :
    # underscore autorisé en première position).
    USERNAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]*$")

    def validate_username(self, username: str) -> bool:
        """Validate username using QML pattern."""
        return bool(self.USERNAME_PATTERN.match(username)) and len(username) > 0

    @pytest.mark.parametrize(
        "username",
        [
            "john",
            "user123",
            "my_user",
            "my-user",
            "a",
            "user_name_123",
            "test-user-name",
            "a1b2c3",
            "z",
            "_system",
            "_user",
        ],
    )
    def test_valid_usernames(self, username: str) -> None:
        """Valid POSIX usernames should pass validation."""
        assert self.validate_username(username), f"'{username}' should be valid"

    @pytest.mark.parametrize(
        "username,reason",
        [
            ("", "empty string"),
            ("John", "uppercase letter"),
            ("JOHN", "all uppercase"),
            ("123user", "starts with digit"),
            ("-user", "starts with hyphen"),
            ("user name", "contains space"),
            ("user@name", "contains @ symbol"),
            ("user.name", "contains dot"),
            ("user#name", "contains hash"),
            ("user$name", "contains dollar sign"),
            ("User", "capital first letter"),
            ("userNAME", "mixed case"),
        ],
    )
    def test_invalid_usernames(self, username: str, reason: str) -> None:
        """Invalid usernames should fail validation."""
        assert not self.validate_username(username), f"'{username}' ({reason}) should be invalid"

    def test_username_max_length(self) -> None:
        """Username at max length (32 chars) should still match pattern."""
        max_username = "a" * 32
        assert self.USERNAME_PATTERN.match(max_username)

    def test_username_edge_cases(self) -> None:
        """Test edge cases for username validation."""
        # Single character valid
        assert self.validate_username("a")
        assert self.validate_username("z")

        # Numbers after first char are valid
        assert self.validate_username("a1")
        assert self.validate_username("user0")
        assert self.validate_username("user9")

        # Combinations of special chars
        assert self.validate_username("a-_")
        assert self.validate_username("user-name_123")


class TestHostnameValidation:
    """
    Tests for hostname validation pattern.

    RFC 1123 hostname requirements:
    - Must start with a lowercase letter
    - Can contain lowercase letters, digits, and hyphens
    - Cannot end with a hyphen
    - Max 63 characters per label, 253 total
    """

    # Pattern from UsersView.qml (simplified for single-label hostname)
    HOSTNAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")

    def validate_hostname(self, hostname: str) -> bool:
        """Validate hostname using QML pattern."""
        if not hostname or len(hostname) > 253:
            return False
        # Check pattern
        if not self.HOSTNAME_PATTERN.match(hostname):
            return False
        # RFC 1123: cannot end with hyphen
        return not hostname.endswith("-")

    @pytest.mark.parametrize(
        "hostname",
        [
            "localhost",
            "mypc",
            "server01",
            "web-server",
            "a",
            "srv123",
            "my-awesome-pc",
            "workstation1",
        ],
    )
    def test_valid_hostnames(self, hostname: str) -> None:
        """Valid RFC 1123 hostnames should pass validation."""
        assert self.validate_hostname(hostname), f"'{hostname}' should be valid"

    @pytest.mark.parametrize(
        "hostname,reason",
        [
            ("", "empty string"),
            ("MyPC", "uppercase letters"),
            ("LOCALHOST", "all uppercase"),
            ("123server", "starts with digit"),
            ("-server", "starts with hyphen"),
            ("server-", "ends with hyphen"),
            ("my_server", "contains underscore"),
            ("my server", "contains space"),
            ("my.server", "contains dot (use for FQDN only)"),
            ("server@domain", "contains @ symbol"),
        ],
    )
    def test_invalid_hostnames(self, hostname: str, reason: str) -> None:
        """Invalid hostnames should fail validation."""
        assert not self.validate_hostname(hostname), f"'{hostname}' ({reason}) should be invalid"

    def test_hostname_max_length(self) -> None:
        """Hostname exceeding 253 chars should fail."""
        long_hostname = "a" * 254
        assert not self.validate_hostname(long_hostname)

        # At limit should pass
        max_hostname = "a" * 253
        assert self.validate_hostname(max_hostname)


class TestPasswordCriteria:
    """
    Tests for password criteria validation.

    NIST SP 800-63B inspired criteria:
    - Minimum 8 characters
    - At least one uppercase letter (recommended)
    - At least one lowercase letter (recommended)
    - At least one number (recommended)
    - At least one special character (recommended)
    """

    def check_min_length(self, password: str, min_len: int = 8) -> bool:
        """Check minimum password length."""
        return len(password) >= min_len

    def check_has_uppercase(self, password: str) -> bool:
        """Check for at least one uppercase letter."""
        return bool(re.search(r"[A-Z]", password))

    def check_has_lowercase(self, password: str) -> bool:
        """Check for at least one lowercase letter."""
        return bool(re.search(r"[a-z]", password))

    def check_has_number(self, password: str) -> bool:
        """Check for at least one digit."""
        return bool(re.search(r"[0-9]", password))

    def check_has_special(self, password: str) -> bool:
        """Check for at least one special character."""
        return bool(re.search(r"[^a-zA-Z0-9]", password))

    def calculate_strength(self, password: str) -> int:
        """Calculate password strength (0-100) matching QML logic."""
        if not password:
            return 0

        strength = 0
        if self.check_min_length(password):
            strength += 25
        if len(password) >= 12:
            strength += 25
        if self.check_has_lowercase(password) and self.check_has_uppercase(password):
            strength += 25
        if self.check_has_number(password):
            strength += 15
        if self.check_has_special(password):
            strength += 10

        return min(100, strength)

    # Minimum length tests
    @pytest.mark.parametrize(
        "password,expected",
        [
            ("", False),
            ("1234567", False),  # 7 chars
            ("12345678", True),  # 8 chars
            ("123456789", True),  # 9 chars
            ("a" * 100, True),  # long password
        ],
    )
    def test_min_length_criterion(self, password: str, expected: bool) -> None:
        """Test minimum length criterion (8 characters)."""
        assert self.check_min_length(password) == expected

    # Uppercase tests
    @pytest.mark.parametrize(
        "password,expected",
        [
            ("lowercase", False),
            ("UPPERCASE", True),
            ("MixedCase", True),
            ("with1number", False),
            ("With1Number", True),
            ("123456789", False),
        ],
    )
    def test_uppercase_criterion(self, password: str, expected: bool) -> None:
        """Test uppercase letter criterion."""
        assert self.check_has_uppercase(password) == expected

    # Lowercase tests
    @pytest.mark.parametrize(
        "password,expected",
        [
            ("lowercase", True),
            ("UPPERCASE", False),
            ("MixedCase", True),
            ("123456789", False),
            ("ABC123abc", True),
        ],
    )
    def test_lowercase_criterion(self, password: str, expected: bool) -> None:
        """Test lowercase letter criterion."""
        assert self.check_has_lowercase(password) == expected

    # Number tests
    @pytest.mark.parametrize(
        "password,expected",
        [
            ("nodigits", False),
            ("has1digit", True),
            ("123456789", True),
            ("ALLCAPS", False),
            ("Mix3dC4se", True),
        ],
    )
    def test_number_criterion(self, password: str, expected: bool) -> None:
        """Test number criterion."""
        assert self.check_has_number(password) == expected

    # Special character tests
    @pytest.mark.parametrize(
        "password,expected",
        [
            ("nospecial", False),
            ("has@special", True),
            ("has!special", True),
            ("has#special", True),
            ("has$special", True),
            ("has%special", True),
            ("has&special", True),
            ("has*special", True),
            ("has-special", True),
            ("has_special", True),
            ("has.special", True),
            ("has space", True),  # space is special
            ("AlphaNum123", False),
        ],
    )
    def test_special_character_criterion(self, password: str, expected: bool) -> None:
        """Test special character criterion."""
        assert self.check_has_special(password) == expected

    # Strength calculation tests
    @pytest.mark.parametrize(
        "password,min_strength,max_strength",
        [
            ("", 0, 0),  # empty
            ("short", 0, 0),  # too short
            ("longpass", 25, 25),  # 8 chars, no criteria
            ("LongPass", 50, 50),  # 8 chars + mixed case
            ("LongPass1", 65, 65),  # 8 chars + mixed case + number
            ("LongPass1!", 75, 75),  # 8 chars + mixed + number + special
            ("VeryLongPass", 75, 75),  # 12+ chars + mixed case
            ("VeryLongPass1", 90, 90),  # 12+ chars + mixed case + number
            ("VeryLongPass1!", 100, 100),  # all criteria met
        ],
    )
    def test_password_strength_calculation(
        self, password: str, min_strength: int, max_strength: int
    ) -> None:
        """Test password strength calculation matches expected ranges."""
        strength = self.calculate_strength(password)
        assert min_strength <= strength <= max_strength, (
            f"Password '{password}' strength {strength} not in [{min_strength}, {max_strength}]"
        )

    def test_strength_categories(self) -> None:
        """Test that strength values map to correct categories."""
        # Weak: < 40
        assert self.calculate_strength("weak123") < 40  # too short

        # Medium: 40-69
        medium_pwd = "LongPass"  # 8 chars + mixed case = 50
        medium_strength = self.calculate_strength(medium_pwd)
        assert 40 <= medium_strength < 70, f"Medium password strength: {medium_strength}"

        # Strong: >= 70
        strong_pwd = "VeryLongPass1!"  # 12+ chars + mixed + number + special
        strong_strength = self.calculate_strength(strong_pwd)
        assert strong_strength >= 70, f"Strong password strength: {strong_strength}"


class TestPasswordsMatch:
    """Tests for password confirmation matching."""

    def passwords_match(self, password: str, confirm: str) -> bool:
        """Check if passwords match (QML logic)."""
        return password == confirm and len(password) > 0

    def test_matching_passwords(self) -> None:
        """Matching non-empty passwords should return True."""
        assert self.passwords_match("secret123", "secret123")
        assert self.passwords_match("a", "a")
        assert self.passwords_match("Complex!Pass123", "Complex!Pass123")

    def test_non_matching_passwords(self) -> None:
        """Non-matching passwords should return False."""
        assert not self.passwords_match("password1", "password2")
        assert not self.passwords_match("secret", "Secret")  # case sensitive
        assert not self.passwords_match("pass word", "password")  # space matters

    def test_empty_passwords(self) -> None:
        """Empty passwords should not match (even if both empty)."""
        assert not self.passwords_match("", "")
        assert not self.passwords_match("password", "")
        assert not self.passwords_match("", "password")


class TestCanProceed:
    """Tests for the canProceed validation combining all criteria."""

    # Aligné sur UsersJob.USERNAME_PATTERN (underscore autorisé en 1ère position).
    USERNAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]*$")
    HOSTNAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")

    def validate_username(self, username: str) -> bool:
        """Validate username."""
        return bool(self.USERNAME_PATTERN.match(username)) and len(username) > 0

    def validate_hostname(self, hostname: str) -> bool:
        """Validate hostname."""
        if not hostname:
            return False
        if not self.HOSTNAME_PATTERN.match(hostname):
            return False
        return not hostname.endswith("-")

    def validate_password(self, password: str) -> bool:
        """Validate password (min 8 chars)."""
        return len(password) >= 8

    def passwords_match(self, password: str, confirm: str) -> bool:
        """Check password match."""
        return password == confirm and len(password) > 0

    def can_proceed(
        self, username: str, hostname: str, password: str, password_confirm: str
    ) -> bool:
        """Combined validation matching QML canProceed property."""
        return (
            self.validate_username(username)
            and self.validate_hostname(hostname)
            and self.validate_password(password)
            and self.passwords_match(password, password_confirm)
        )

    def test_can_proceed_valid_data(self) -> None:
        """Valid data should allow proceeding."""
        assert self.can_proceed(
            username="john",
            hostname="mypc",
            password="secret123",
            password_confirm="secret123",
        )

    def test_cannot_proceed_invalid_username(self) -> None:
        """Invalid username should block proceeding."""
        assert not self.can_proceed(
            username="John",  # uppercase
            hostname="mypc",
            password="secret123",
            password_confirm="secret123",
        )

    def test_cannot_proceed_invalid_hostname(self) -> None:
        """Invalid hostname should block proceeding."""
        assert not self.can_proceed(
            username="john",
            hostname="-mypc",  # starts with hyphen
            password="secret123",
            password_confirm="secret123",
        )

    def test_cannot_proceed_short_password(self) -> None:
        """Short password should block proceeding."""
        assert not self.can_proceed(
            username="john",
            hostname="mypc",
            password="short",  # < 8 chars
            password_confirm="short",
        )

    def test_cannot_proceed_password_mismatch(self) -> None:
        """Password mismatch should block proceeding."""
        assert not self.can_proceed(
            username="john",
            hostname="mypc",
            password="secret123",
            password_confirm="different",
        )

    def test_cannot_proceed_empty_fields(self) -> None:
        """Empty fields should block proceeding."""
        assert not self.can_proceed(
            username="",
            hostname="mypc",
            password="secret123",
            password_confirm="secret123",
        )
        assert not self.can_proceed(
            username="john",
            hostname="",
            password="secret123",
            password_confirm="secret123",
        )
        assert not self.can_proceed(
            username="john",
            hostname="mypc",
            password="",
            password_confirm="",
        )
