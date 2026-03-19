# Reports Archive Feature - Implementation Guide

## Overview
This feature provides a comprehensive reports archive system where teachers can:
- View all generated school reports organized by class folders
- Search for student reports
- Edit, delete, download individual reports
- Bulk select and download multiple reports
- Download entire class reports as ZIP files
- Automatic position assignment for FORM 1 & FORM 2 students
- Reports sorted from highest to lowest scores

## Backend Implementation (COMPLETED)

### 1. Database Model (`backend/models.py`)
Added `SchoolReport` model with fields:
- `id`, `student_id`, `student_name`, `student_class`
- `term`, `academic_year`
- `total_subjects`, `average_score`, `aggregate_points`
- `position` (for FORM 1&2 ranking)
- `report_data` (JSON), `pdf_data` (Base64)
- `created_at`, `updated_at`

### 2. API Endpoints (`backend/app.py`)

#### Report Management:
- `GET /api/reports` - List all reports with filters (class, term, year, search)
- `GET /api/reports/{id}` - Get full report details
- `DELETE /api/reports/{id}` - Delete a report
- `POST /api/reports/generate` - Generate reports for a class
- `POST /api/reports/assign-positions` - Assign positions for FORM 1&2
- `GET /api/reports/download/{id}` - Download individual PDF
- `POST /api/reports/download-batch` - Download selected reports as ZIP
- `POST /api/reports/download-class` - Download entire class as ZIP

#### Key Features:
- Automatic sorting by average_score (highest to lowest)
- Position assignment with tie handling for FORM 1&2
- PDF generation using ReportLab
- ZIP file creation for bulk downloads

### 3. Dependencies (`backend/requirements.txt`)
Added: `reportlab==4.0.4`

## Frontend Implementation (TODO)

### HTML Structure to Add in LIDOMA.HTML

Add this after the Analytics tab section (before Settings tab):

```html
<!-- Tab: Reports Archive -->
<main id="tab-reports-archive" class="max-w-7xl mx-auto p-6 hidden fade-in">
    <!-- Header with Actions -->
    <div class="rounded-xl p-6 mb-6 card-hover" style="background:#ffffff;border:1px solid #e2e8f0;">
        <div class="flex items-center justify-between flex-wrap gap-4">
            <h2 class="font-display text-xl font-bold" style="color:#1e293b;">
                <span class="flex items-center gap-2">
                    <i data-lucide="folder-archive" style="width:24px;height:24px;color:#4f46e5;"></i>
                    School Reports Archive
                </span>
            </h2>
            <div class="flex items-center gap-3">
                <button onclick="openGenerateReportModal()" 
                    class="rounded-lg px-4 py-2 text-sm font-bold transition-all"
                    style="background:#4f46e5;color:#ffffff;">
                    <span class="flex items-center gap-2">
                        <i data-lucide="plus-circle" style="width:16px;height:16px;"></i>
                        Generate Reports
                    </span>
                </button>
                <button onclick="downloadSelectedReports()" id="btn-download-selected" disabled
                    class="rounded-lg px-4 py-2 text-sm font-bold transition-all"
                    style="background:#059669;color:#ffffff;">
                    <span class="flex items-center gap-2">
                        <i data-lucide="download" style="width:16px;height:16px;"></i>
                        Download Selected (<span id="selected-count">0</span>)
                    </span>
                </button>
            </div>
        </div>
        
        <!-- Search and Filters -->
        <div class="mt-4 grid grid-cols-1 md:grid-cols-4 gap-4">
            <div class="md:col-span-2">
                <input type="text" id="report-search" placeholder="Search by student name or ID..." 
                    class="w-full rounded-lg px-4 py-2.5 text-sm"
                    style="border:1px solid #cbd5e1;color:#1e293b;"
                    oninput="debouncedSearch()">
            </div>
            <div>
                <select id="filter-class" onchange="loadReports()" 
                    class="w-full rounded-lg px-3 py-2.5 text-sm"
                    style="border:1px solid #cbd5e1;color:#1e293b;">
                    <option value="">All Classes</option>
                    <option value="FORM 1">FORM 1</option>
                    <option value="FORM 2">FORM 2</option>
                    <option value="FORM 3">FORM 3</option>
                    <option value="FORM 4">FORM 4</option>
                </select>
            </div>
            <div>
                <select id="filter-term" onchange="loadReports()" 
                    class="w-full rounded-lg px-3 py-2.5 text-sm"
                    style="border:1px solid #cbd5e1;color:#1e293b;">
                    <option value="">All Terms</option>
                    <option value="First Term">First Term</option>
                    <option value="Second Term">Second Term</option>
                    <option value="Third Term">Third Term</option>
                </select>
            </div>
        </div>
    </div>

    <!-- Class Folders -->
    <div id="class-folders-container" class="space-y-6">
        <!-- Dynamically populated class sections -->
    </div>

    <!-- Empty State -->
    <div id="reports-empty-state" class="text-center py-20 hidden">
        <i data-lucide="inbox" style="width:64px;height:64px;color:#cbd5e1;margin:0 auto;display:block;"></i>
        <p class="mt-4 text-lg font-semibold" style="color:#64748b;">No reports found</p>
        <p class="text-sm mt-2" style="color:#94a3b8;">Generate reports for a class to see them here</p>
    </div>
</main>
```

### Modals to Add:

1. **Generate Report Modal** - For selecting class, term, year and assigning positions
2. **View/Edit Report Modal** - For viewing and editing report details
3. **Bulk Actions Confirmation** - For batch operations

### JavaScript Functions to Implement:

```javascript
// Load and display all reports grouped by class
async function loadReports() {
    const search = document.getElementById('report-search').value;
    const studentClass = document.getElementById('filter-class').value;
    const term = document.getElementById('filter-term').value;
    
    try {
        const params = new URLSearchParams({ search, student_class: studentClass, term });
        const reports = await apiRequest(`/api/reports?${params}`);
        renderReportsByClass(reports);
    } catch (error) {
        showToast('Failed to load reports: ' + error.message, 'error');
    }
}

// Render reports grouped by class folders
function renderReportsByClass(reports) {
    const container = document.getElementById('class-folders-container');
    const classes = ['FORM 1', 'FORM 2', 'FORM 3', 'FORM 4'];
    
    container.innerHTML = '';
    
    classes.forEach(cls => {
        const classReports = reports.filter(r => r.student_class === cls);
        if (classReports.length === 0) return;
        
        const folderHTML = createClassFolderHTML(cls, classReports);
        container.insertAdjacentHTML('beforeend', folderHTML);
    });
    
    lucide.createIcons();
}

// Create collapsible class folder with reports table
function createClassFolderHTML(className, reports) {
    return `
        <div class="rounded-xl overflow-hidden" style="background:#ffffff;border:1px solid #e2e8f0;">
            <div class="px-6 py-4 flex items-center justify-between cursor-pointer hover:bg-slate-50"
                onclick="toggleClassFolder('${className}')">
                <div class="flex items-center gap-3">
                    <i data-lucide="chevron-down" id="chevron-${className}" 
                        style="width:20px;height:20px;color:#64748b;"></i>
                    <i data-lucide="folder" style="width:24px;height:24px;color:#4f46e5;"></i>
                    <div>
                        <h3 class="font-display text-lg font-bold" style="color:#1e293b;">${className}</h3>
                        <p class="text-xs" style="color:#64748b;">${reports.length} reports</p>
                    </div>
                </div>
                <button onclick="event.stopPropagation(); downloadClassReports('${className}')"
                    class="rounded-lg px-3 py-1.5 text-sm font-semibold transition-colors"
                    style="background:#059669;color:#ffffff;">
                    <span class="flex items-center gap-1.5">
                        <i data-lucide="download" style="width:14px;height:14px;"></i>
                        Download All
                    </span>
                </button>
            </div>
            
            <div id="class-${className}" class="class-folder-content px-6 pb-4">
                <table class="w-full text-sm">
                    <thead>
                        <tr style="background:#f8fafc;">
                            <th class="text-left px-4 py-3 w-10">
                                <input type="checkbox" onchange="toggleClassSelection('${className}', this.checked)">
                            </th>
                            <th class="text-left px-4 py-3 font-semibold" style="color:#64748b;">Position</th>
                            <th class="text-left px-4 py-3 font-semibold" style="color:#64748b;">Student Name</th>
                            <th class="text-left px-4 py-3 font-semibold" style="color:#64748b;">Student ID</th>
                            <th class="text-center px-4 py-3 font-semibold" style="color:#64748b;">Term</th>
                            <th class="text-center px-4 py-3 font-semibold" style="color:#64748b;">Average</th>
                            <th class="text-center px-4 py-3 font-semibold" style="color:#64748b;">Aggregate</th>
                            <th class="text-center px-4 py-3 font-semibold" style="color:#64748b;">Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${reports.map(report => createReportRowHTML(report)).join('')}
                    </tbody>
                </table>
            </div>
        </div>
    `;
}

// Create individual report row
function createReportRowHTML(report) {
    const positionDisplay = report.position ? `#${report.position}` : '-';
    const isForm1Or2 = ['FORM 1', 'FORM 2'].includes(report.student_class);
    
    return `
        <tr style="border-top:1px solid #f1f5f9;" class="hover:bg-slate-50/50">
            <td class="px-4 py-3">
                <input type="checkbox" class="report-checkbox" 
                    data-report-id="${report.id}" 
                    onchange="updateSelectedCount()">
            </td>
            <td class="px-4 py-3">
                ${isForm1Or2 ? `<span class="font-bold ${report.position && report.position <= 3 ? 'text-indigo-600' : ''}">${positionDisplay}</span>` : '<span class="text-slate-400">-</span>'}
            </td>
            <td class="px-4 py-3 font-medium" style="color:#1e293b;">${report.student_name}</td>
            <td class="px-4 py-3 font-mono text-xs" style="color:#64748b;">${report.student_id}</td>
            <td class="text-center px-4 py-3 text-xs" style="color:#64748b;">${report.term}</td>
            <td class="text-center px-4 py-3">
                <span class="inline-block px-2 py-0.5 rounded font-semibold text-xs" 
                    style="background:#eff6ff;color:#2563eb;">${report.average_score.toFixed(1)}%</span>
            </td>
            <td class="text-center px-4 py-3">
                <span class="inline-block px-2 py-0.5 rounded font-semibold text-xs" 
                    style="background:#f5f3ff;color:#7c3aed;">${report.aggregate_points.toFixed(1)}</span>
            </td>
            <td class="text-center px-4 py-3">
                <div class="flex items-center justify-center gap-2">
                    <button onclick="viewReport(${report.id})" title="View"
                        class="rounded-lg p-1.5 transition-colors" 
                        style="background:#eff6ff;color:#2563eb;">
                        <i data-lucide="eye" style="width:14px;height:14px;"></i>
                    </button>
                    <button onclick="downloadReport(${report.id})" title="Download"
                        class="rounded-lg p-1.5 transition-colors" 
                        style="background:#f0fdf4;color:#059669;">
                        <i data-lucide="download" style="width:14px;height:14px;"></i>
                    </button>
                    ${report.student_class === 'FORM 1' || report.student_class === 'FORM 2' ? `
                    <button onclick="assignPosition(${report.id})" title="Assign Position"
                        class="rounded-lg p-1.5 transition-colors" 
                        style="background:#fef3c7;color:#d97706;">
                        <i data-lucide="award" style="width:14px;height:14px;"></i>
                    </button>
                    ` : ''}
                    <button onclick="deleteReport(${report.id})" title="Delete"
                        class="rounded-lg p-1.5 transition-colors" 
                        style="background:#fef2f2;color:#dc2626;">
                        <i data-lucide="trash-2" style="width:14px;height:14px;"></i>
                    </button>
                </div>
            </td>
        </tr>
    `;
}

// Toggle class folder collapse
function toggleClassFolder(className) {
    const content = document.getElementById(`class-${className}`);
    const chevron = document.getElementById(`chevron-${className}`);
    
    if (content.style.display === 'none') {
        content.style.display = 'block';
        chevron.setAttribute('data-lucide', 'chevron-down');
    } else {
        content.style.display = 'none';
        chevron.setAttribute('data-lucide', 'chevron-right');
    }
    lucide.createIcons();
}

// Generate reports for a class
async function openGenerateReportModal() {
    // Show modal with class, term, year selection
    // Include "Assign Positions" checkbox for FORM 1&2
}

async function generateReports() {
    // Call POST /api/reports/generate
}

// View report details
async function viewReport(reportId) {
    // Fetch and show in modal
}

// Download single report
async function downloadReport(reportId) {
    window.open(`${API_BASE}/api/reports/download/${reportId}`, '_blank');
}

// Download selected reports
async function downloadSelectedReports() {
    const selectedIds = Array.from(document.querySelectorAll('.report-checkbox:checked'))
        .map(cb => cb.dataset.reportId);
    
    if (selectedIds.length === 0) return;
    
    try {
        const blob = await apiRequest('/api/reports/download-batch', {
            method: 'POST',
            body: JSON.stringify({ report_ids: selectedIds })
        });
        
        // Trigger download
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'reports_batch.zip';
        a.click();
    } catch (error) {
        showToast('Failed to download: ' + error.message, 'error');
    }
}

// Download entire class
async function downloadClassReports(className) {
    const term = document.getElementById('filter-term').value;
    const year = document.getElementById('filter-year').value;
    
    if (!term || !year) {
        showToast('Please select term and academic year', 'warning');
        return;
    }
    
    try {
        const blob = await apiRequest('/api/reports/download-class', {
            method: 'POST',
            body: JSON.stringify({ student_class: className, term, academic_year: year })
        });
        
        // Trigger download
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${className}_${term}_${year}_Reports.zip`;
        a.click();
    } catch (error) {
        showToast('Failed to download: ' + error.message, 'error');
    }
}

// Assign position for FORM 1&2
async function assignPosition(reportId) {
    // Call endpoint to assign/update position
}

// Delete report
async function deleteReport(reportId) {
    if (!confirm('Delete this report permanently?')) return;
    
    try {
        await apiRequest(`/api/reports/${reportId}`, { method: 'DELETE' });
        showToast('Report deleted', 'success');
        loadReports();
    } catch (error) {
        showToast('Failed to delete: ' + error.message, 'error');
    }
}

// Selection management
function toggleClassSelection(className, checked) {
    const checkboxes = document.querySelectorAll(`#class-${className} .report-checkbox`);
    checkboxes.forEach(cb => cb.checked = checked);
    updateSelectedCount();
}

function updateSelectedCount() {
    const count = document.querySelectorAll('.report-checkbox:checked').length;
    document.getElementById('selected-count').textContent = count;
    document.getElementById('btn-download-selected').disabled = count === 0;
}

// Debounced search
let searchTimeout;
function debouncedSearch() {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(loadReports, 300);
}
```

## Usage Workflow

### Generating Reports:
1. Click "Generate Reports" button
2. Select Class, Term, Academic Year
3. Check "Assign Positions" for FORM 1&2
4. Click Generate - creates reports for all students in that class

### Viewing Reports:
- Reports automatically grouped by class folders
- Each folder shows student count
- Click folder header to expand/collapse
- Sorted by highest to lowest average

### Position Assignment (FORM 1&2 only):
1. Filter to FORM 1 or FORM 2
2. Click "Assign Position" button on any student
3. System automatically assigns positions based on average scores
4. Handles ties (same position for same averages)

### Downloading:
- **Individual**: Click download icon on any report
- **Batch**: Select multiple checkboxes → "Download Selected"
- **Entire Class**: Click "Download All" on class folder

### Searching:
- Type student name or ID in search box
- Results update automatically (300ms debounce)
- Combine with class/term filters

## Next Steps

1. Add the HTML sections to LIDOMA.HTML
2. Implement all JavaScript functions
3. Add modal HTML for generate/view/edit actions
4. Test with sample data
5. Add CSS styles for any new components
6. Update icons (add folder-archive, award icons to Lucide)

Would you like me to continue with the complete frontend implementation?
