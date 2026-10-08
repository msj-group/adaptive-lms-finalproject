ADMIN_NAV_SECTIONS = [
    {
        "label": "Academic setup",
        "links": [
            {"label": "Academic Terms", "endpoint": "admin.academic_terms_list"},
            {"label": "Levels", "endpoint": "admin.levels_list"},
            {"label": "Courses", "endpoint": "admin.courses_list"},
            {"label": "Rooms", "endpoint": "admin.rooms_list"},
        ],
    },
    {
        "label": "People",
        "links": [
            {"label": "Students", "endpoint": "admin.students_list"},
            {"label": "Teachers", "endpoint": "admin.teachers_list"},
            ]},
    {"label": "Operations", "links": [
            {"label": "Groups", "endpoint": "admin.groups_list"},
            {"label": "Schedules", "endpoint": "admin.schedules_overview"},
            {"label": "Attendance", "endpoint": "admin.attendance_overview"},
            {"label": "Grades", "endpoint": "admin.gradebook_overview"},
            {"label": "Announcements", "endpoint": "admin.announcements_overview"},
            {"label": "Calendar", "endpoint": "admin.calendar"},
        ],
    },
    {
        "label": "Finance",
        "links": [
            {"label": "Student Accounts", "endpoint": "admin.student_accounts"},
            {"label": "Invoices", "endpoint": "admin.invoice_register"},
            {"label": "Collections & payouts", "endpoint": "admin.payments_overview"},
            {"label": "Financial reports", "endpoint": "admin.financial_reports_index"},
            {"label": "Historical deleted records", "endpoint": "admin.deleted_financial_records"},
            # No research entry: research management lives only in the
            # separate Researcher workspace (Phase 6 replacement).
        ],
    },
]
