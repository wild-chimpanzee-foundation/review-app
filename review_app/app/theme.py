from nicegui import ui

# Modern color palette
COLORS = {
    "primary": "#3b82f6",    # Modern Blue
    "secondary": "#10b981",  # Emerald
    "accent": "#6366f1",     # Indigo
    "dark": "#0f172a",       # Slate 900
    "positive": "#22c55e",
    "negative": "#ef4444",
    "info": "#3b82f6",
    "warning": "#f59e0b",
}


def apply_theme():
    # Set UI colors
    ui.colors(
        primary=COLORS["primary"],
        secondary=COLORS["secondary"],
        accent=COLORS["accent"],
        dark=COLORS["dark"],
        positive=COLORS["positive"],
        negative=COLORS["negative"],
        info=COLORS["info"],
        warning=COLORS["warning"],
    )

    # Add modern system font stack and global styles
    ui.add_head_html(
        """
        <style>
            :root {
                --q-primary: """ + COLORS["primary"] + """;
            }
            
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol";
                background-color: #f8fafc;
            }
            
            .body--dark {
                background-color: #0f172a !important;
            }
            
            .q-card {
                border-radius: 12px;
                box-shadow: 0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1) !important;
                border: 1px solid #e2e8f0;
            }
            
            .body--dark .q-card {
                border: 1px solid #1e293b;
                background-color: #1e293b !important;
            }
            
            .q-header {
                box-shadow: 0 1px 2px 0 rgb(0 0 0 / 0.05) !important;
            }
            
            /* Modern button styling */
            .q-btn {
                border-radius: 8px;
                text-transform: none;
                font-weight: 500;
                letter-spacing: normal;
            }
        </style>
    """,
        shared=True,
    )
