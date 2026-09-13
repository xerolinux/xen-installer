#!/usr/bin/env python3
"""
Fill Qt Linguist .ts files with translations.

This script automatically fills empty <translation> tags in Qt .ts files
with appropriate translations for each target language.
"""

import re
from pathlib import Path
from typing import Dict

# Translation dictionaries for each language
TRANSLATIONS = {
    "de_DE": {
        # LocaleView
        "Country & Language": "Land & Sprache",
        "Configure your system language, timezone, and keyboard layout": "Konfigurieren Sie Ihre Systemsprache, Zeitzone und Tastaturlayout",
        "System Language": "Systemsprache",
        "Select your preferred system language and locale": "Wählen Sie Ihre bevorzugte Systemsprache und Gebietsschema",
        "Select language...": "Sprache auswählen...",
        "Search languages...": "Sprachen suchen...",
        "Timezone": "Zeitzone",
        "Select your timezone for accurate time display": "Wählen Sie Ihre Zeitzone für genaue Zeitanzeige",
        "Select timezone...": "Zeitzone auswählen...",
        "Search timezones...": "Zeitzonen suchen...",
        "Keyboard Configuration": "Tastaturkonfiguration",
        "Select your keyboard layout and variant": "Wählen Sie Ihr Tastaturlayout und Variante",
        "Layout": "Layout",
        "Select...": "Auswählen...",
        "Search layouts...": "Layouts suchen...",
        "Variant": "Variante",
        "Default": "Standard",
        "Test:": "Test:",
        "Type to test keyboard...": "Tippen Sie, um Tastatur zu testen...",

        # SearchableComboBox
        "Type to search...": "Tippen Sie zum Suchen...",
        "%1 items": "%1 Elemente",
        "%1 of %2 items": "%1 von %2 Elementen",
        "No results found": "Keine Ergebnisse gefunden",

        # SummaryView
        "Review Installation": "Installation überprüfen",
        "Please review your selections before starting the installation": "Bitte überprüfen Sie Ihre Auswahl vor Beginn der Installation",
        "System": "System",
        "Computer Name:": "Computername:",
        "Not set": "Nicht festgelegt",
        "Edit": "Bearbeiten",
        "Locale & Keyboard": "Gebietsschema & Tastatur",
        "Language:": "Sprache:",
        "Timezone:": "Zeitzone:",
        "Keyboard Layout:": "Tastaturlayout:",
        "User Account": "Benutzerkonto",
        "Username:": "Benutzername:",
        "Full Name:": "Vollständiger Name:",
        "Administrator:": "Administrator:",
        "Yes": "Ja",
        "No": "Nein",
        "Auto Login:": "Automatische Anmeldung:",
        "Enabled": "Aktiviert",
        "Disabled": "Deaktiviert",
        "Storage": "Speicher",
        "Installation Disk:": "Installationsfestplatte:",
        "Size:": "Größe:",
        "Unknown": "Unbekannt",
        "Partitioning:": "Partitionierung:",
        "Automatic": "Automatisch",
        "Manual": "Manuell",
        "Ready to Install": "Bereit zur Installation",
        "The installation will begin once you click the Install button. This process will modify your disk and cannot be undone. Please ensure all data is backed up.": "Die Installation beginnt, sobald Sie auf die Schaltfläche Installieren klicken. Dieser Vorgang ändert Ihre Festplatte und kann nicht rückgängig gemacht werden. Bitte stellen Sie sicher, dass alle Daten gesichert sind.",
        "Previous": "Zurück",
        "Install Now": "Jetzt installieren",

        # FinishedView
        "Installation Complete!": "Installation abgeschlossen!",
        "Installation Failed": "Installation fehlgeschlagen",
        "The system has been successfully installed on your computer": "Das System wurde erfolgreich auf Ihrem Computer installiert",
        "An error occurred during installation": "Ein Fehler ist während der Installation aufgetreten",
        "Installation Summary": "Installationszusammenfassung",
        "Distribution:": "Distribution:",
        "Installation Target:": "Installationsziel:",
        "Installation Time:": "Installationszeit:",
        "Packages Installed:": "Installierte Pakete:",
        "Error Details": "Fehlerdetails",
        "An unknown error occurred during installation": "Ein unbekannter Fehler ist während der Installation aufgetreten",
        "View Full Logs": "Vollständige Protokolle anzeigen",
        "Reboot Now": "Jetzt neu starten",
        "Shutdown": "Herunterfahren",
        "Continue": "Weiter",
        "Retry Installation": "Installation wiederholen",
        "Exit Installer": "Installer beenden",
        "Please remove the installation media before rebooting": "Bitte entfernen Sie das Installationsmedium vor dem Neustart",
        "Check the logs for more details about the error": "Überprüfen Sie die Protokolle für weitere Details zum Fehler",

        # Main
        "Back": "Zurück",
        "Next": "Weiter",
        "Install": "Installieren",
    },
    "es_ES": {
        # LocaleView
        "Country & Language": "País e idioma",
        "Configure your system language, timezone, and keyboard layout": "Configure el idioma del sistema, la zona horaria y la distribución del teclado",
        "System Language": "Idioma del sistema",
        "Select your preferred system language and locale": "Seleccione su idioma y configuración regional preferidos",
        "Select language...": "Seleccionar idioma...",
        "Search languages...": "Buscar idiomas...",
        "Timezone": "Zona horaria",
        "Select your timezone for accurate time display": "Seleccione su zona horaria para una visualización precisa del tiempo",
        "Select timezone...": "Seleccionar zona horaria...",
        "Search timezones...": "Buscar zonas horarias...",
        "Keyboard Configuration": "Configuración del teclado",
        "Select your keyboard layout and variant": "Seleccione la distribución y variante de su teclado",
        "Layout": "Distribución",
        "Select...": "Seleccionar...",
        "Search layouts...": "Buscar distribuciones...",
        "Variant": "Variante",
        "Default": "Predeterminado",
        "Test:": "Prueba:",
        "Type to test keyboard...": "Escriba para probar el teclado...",

        # SearchableComboBox
        "Type to search...": "Escriba para buscar...",
        "%1 items": "%1 elementos",
        "%1 of %2 items": "%1 de %2 elementos",
        "No results found": "No se encontraron resultados",

        # SummaryView
        "Review Installation": "Revisar instalación",
        "Please review your selections before starting the installation": "Por favor, revise sus selecciones antes de iniciar la instalación",
        "System": "Sistema",
        "Computer Name:": "Nombre del equipo:",
        "Not set": "No establecido",
        "Edit": "Editar",
        "Locale & Keyboard": "Configuración regional y teclado",
        "Language:": "Idioma:",
        "Timezone:": "Zona horaria:",
        "Keyboard Layout:": "Distribución del teclado:",
        "User Account": "Cuenta de usuario",
        "Username:": "Nombre de usuario:",
        "Full Name:": "Nombre completo:",
        "Administrator:": "Administrador:",
        "Yes": "Sí",
        "No": "No",
        "Auto Login:": "Inicio de sesión automático:",
        "Enabled": "Habilitado",
        "Disabled": "Deshabilitado",
        "Storage": "Almacenamiento",
        "Installation Disk:": "Disco de instalación:",
        "Size:": "Tamaño:",
        "Unknown": "Desconocido",
        "Partitioning:": "Particionamiento:",
        "Automatic": "Automático",
        "Manual": "Manual",
        "Ready to Install": "Listo para instalar",
        "The installation will begin once you click the Install button. This process will modify your disk and cannot be undone. Please ensure all data is backed up.": "La instalación comenzará una vez que haga clic en el botón Instalar. Este proceso modificará su disco y no se puede deshacer. Asegúrese de que todos los datos estén respaldados.",
        "Previous": "Anterior",
        "Install Now": "Instalar ahora",

        # FinishedView
        "Installation Complete!": "¡Instalación completa!",
        "Installation Failed": "Instalación fallida",
        "The system has been successfully installed on your computer": "El sistema se ha instalado correctamente en su equipo",
        "An error occurred during installation": "Ocurrió un error durante la instalación",
        "Installation Summary": "Resumen de instalación",
        "Distribution:": "Distribución:",
        "Installation Target:": "Destino de instalación:",
        "Installation Time:": "Tiempo de instalación:",
        "Packages Installed:": "Paquetes instalados:",
        "Error Details": "Detalles del error",
        "An unknown error occurred during installation": "Ocurrió un error desconocido durante la instalación",
        "View Full Logs": "Ver registros completos",
        "Reboot Now": "Reiniciar ahora",
        "Shutdown": "Apagar",
        "Continue": "Continuar",
        "Retry Installation": "Reintentar instalación",
        "Exit Installer": "Salir del instalador",
        "Please remove the installation media before rebooting": "Por favor, retire el medio de instalación antes de reiniciar",
        "Check the logs for more details about the error": "Consulte los registros para obtener más detalles sobre el error",

        # Main
        "Back": "Atrás",
        "Next": "Siguiente",
        "Install": "Instalar",
    },
    # Add similar translations for it_IT, pt_BR, ru_RU, zh_CN, ja_JP, ko_KR...
}


def fill_translation(lang: str, source: str) -> str:
    """Get translation for a source string in the target language."""
    translations = TRANSLATIONS.get(lang, {})
    return translations.get(source, "")


def process_ts_file(file_path: Path, lang: str) -> int:
    """Process a .ts file and fill in translations."""
    content = file_path.read_text(encoding="utf-8")

    # Pattern to match <translation type="unfinished"></translation>
    pattern = r'<translation type="unfinished"></translation>'

    # Find all <source> tags and their corresponding content
    source_pattern = r'<source>(.*?)</source>'
    sources = re.findall(source_pattern, content, re.DOTALL)

    count = 0
    for source_text in sources:
        # Unescape XML entities
        source_clean = source_text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        translation = fill_translation(lang, source_clean)

        if translation:
            # Escape XML entities in translation
            translation_escaped = translation.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

            # Replace the next unfinished translation
            content = content.replace(
                '<translation type="unfinished"></translation>',
                f'<translation>{translation_escaped}</translation>',
                1  # Replace only the first occurrence
            )
            count += 1

    # Write back the modified content
    file_path.write_text(content, encoding="utf-8")
    return count


def main():
    """Main entry point."""
    translations_dir = Path(__file__).parent.parent / "src" / "xen" / "gui" / "translations"

    # Priority languages
    priority_langs = ["de_DE", "es_ES", "it_IT", "pt_BR", "ru_RU", "zh_CN", "ja_JP", "ko_KR"]

    for lang in priority_langs:
        ts_file = translations_dir / f"xen_{lang}.ts"
        if ts_file.exists():
            count = process_ts_file(ts_file, lang)
            print(f"Filled {count} translations in {ts_file.name}")
        else:
            print(f"Warning: {ts_file.name} not found")


if __name__ == "__main__":
    main()
