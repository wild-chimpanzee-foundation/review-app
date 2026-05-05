from nicegui import ui

# Modern color palette
COLORS = {
    "primary": "#3b82f6",  # Modern Blue
    "secondary": "#10b981",  # Emerald
    "accent": "#6366f1",  # Indigo
    "dark": "#0f172a",  # Slate 900
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
                --q-primary: """
        + COLORS["primary"]
        + """;
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

            .q-drawer {
                background-color: #f8fafc;
                border-right: 1px solid #e2e8f0;
            }

            .body--dark .q-drawer {
                background-color: #0f172a !important;
                border-right: 1px solid #1e293b;
            }

            .q-drawer--right {
                border-left: 1px solid #e2e8f0;
                border-right: none;
            }

            .body--dark .q-drawer--right {
                border-left: 1px solid #1e293b;
                border-right: none;
            }

            /* Navigation item styling */
            .nav-item {
                margin: 4px 8px;
                border-radius: 8px;
                color: #64748b;
                transition: all 0.2s ease;
            }

            .nav-item:hover {
                background-color: #f1f5f9;
                color: var(--q-primary);
            }

            .body--dark .nav-item {
                color: #94a3b8;
            }

            .body--dark .nav-item:hover {
                background-color: #1e293b;
                color: white;
            }

            .nav-item-active {
                background-color: #eff6ff !important;
                color: var(--q-primary) !important;
                font-weight: 600;
            }

            .body--dark .nav-item-active {
                background-color: #1e293b !important;
                color: #3b82f6 !important;
            }
            
            /* Modern button styling */
            .q-btn {
                border-radius: 8px;
                text-transform: none;
                font-weight: 500;
                letter-spacing: normal;
            }

            /* Theme-aware grey text colors */
            .body--light .text-grey-1 { color: #e2e8f0 !important; }
            .body--light .text-grey-2 { color: #cbd5e1 !important; }
            .body--light .text-grey-3 { color: #94a3b8 !important; }
            .body--light .text-grey-4 { color: #64748b !important; }
            .body--light .text-grey-5 { color: #475569 !important; }
            .body--light .text-grey-6 { color: #334155 !important; }
            .body--light .text-grey-7 { color: #1e293b !important; }
            .body--light .text-grey-8 { color: #0f172a !important; }
            .body--light .text-grey-9 { color: #020617 !important; }
            .body--light .text-grey-10 { color: #000000 !important; }

            .body--dark .text-grey-1 { color: #fafafa !important; }
            .body--dark .text-grey-2 { color: #eeeeee !important; }
            .body--dark .text-grey-3 { color: #e0e0e0 !important; }
            .body--dark .text-grey-4 { color: #bdbdbd !important; }
            .body--dark .text-grey-5 { color: #a0a0a0 !important; }
            .body--dark .text-grey-6 { color: #9e9e9e !important; }
            .body--dark .text-grey-7 { color: #b0b0b0 !important; }
            .body--dark .text-grey-8 { color: #c0c0c0 !important; }
            .body--dark .text-grey-9 { color: #d0d0d0 !important; }
            .body--dark .text-grey-10 { color: #e0e0e0 !important; }

            /* Header select dropdown styling */
            .header-dropdown {
                background: var(--q-primary) !important;
                color: white !important;
            }
            .header-dropdown .q-item {
                color: white !important;
            }
            .header-dropdown .q-item:hover {
                background: rgba(255, 255, 255, 0.1) !important;
            }
        </style>
    """,
        shared=True,
    )
