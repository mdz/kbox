/**
 * Configuration screen functions for kbox web UI.
 */

import { isOperator, currentConfigData, setCurrentConfigData } from './state.js';
import { escapeHtml } from './utils.js';
import { showOperatorPinModal } from './auth.js';

// Toggle configuration screen
export function toggleConfigScreen() {
    const screen = document.getElementById('config-screen');
    const toggleButton = document.getElementById('config-toggle-button');
    
    if (!screen || !toggleButton) return;
    
    // Check if user is operator, prompt for PIN if not
    if (!isOperator) {
        showOperatorPinModal(() => toggleConfigScreen());
        return;
    }
    
    if (screen.classList.contains('hidden')) {
        // Open config screen
        screen.classList.remove('hidden');
        toggleButton.classList.add('active');
        toggleButton.title = 'Close Configuration';
        
        // Load configuration
        loadConfiguration();
    } else {
        // Close config screen
        screen.classList.add('hidden');
        toggleButton.classList.remove('active');
        toggleButton.title = 'Open Configuration';
    }
}

// Load configuration from server
export async function loadConfiguration() {
    const container = document.getElementById('config-form-container');
    const messageDiv = document.getElementById('config-message');
    
    if (!container) return;
    
    try {
        const response = await fetch('/api/config');
        if (!response.ok) {
            throw new Error('Failed to load configuration');
        }
        
        const data = await response.json();
        setCurrentConfigData(data);
        const { values, schema, groups } = data;
        
        // Sort groups by order
        const sortedGroups = Object.entries(groups)
            .sort((a, b) => a[1].order - b[1].order);
        
        // Group schema entries by their group
        const groupedSchema = {};
        for (const [key, fieldSchema] of Object.entries(schema)) {
            const group = fieldSchema.group || 'other';
            if (!groupedSchema[group]) {
                groupedSchema[group] = [];
            }
            groupedSchema[group].push({ key, ...fieldSchema });
        }
        
        // Build HTML for each group
        let html = '<div class="config-container">';
        
        for (const [groupId, groupInfo] of sortedGroups) {
            const fields = groupedSchema[groupId];
            if (!fields || fields.length === 0) continue;
            
            html += `
                <div class="config-group">
                    <div class="config-group-header">${escapeHtml(groupInfo.label)}</div>
                    <div class="config-group-content">
            `;
            
            for (const field of fields) {
                const value = values[field.key] || '';
                html += renderConfigField(field.key, field, value);
            }
            
            html += `
                    </div>
                </div>
            `;
        }
        
        html += '</div>';
        
        container.innerHTML = html;
        
        // Initialize slider value displays
        initializeSliders();
        
        // Add Save and Close buttons to actions container
        const actionsContainer = document.getElementById('config-actions-container');
        if (actionsContainer) {
            actionsContainer.innerHTML = `
                <div class="config-actions">
                    <button onclick="saveConfiguration()">Save Configuration</button>
                    <button onclick="toggleConfigScreen()" class="secondary">Close</button>
                </div>
            `;
        }
        
        if (messageDiv) {
            messageDiv.innerHTML = '';
        }
    } catch (e) {
        console.error('Error loading configuration:', e);
        container.innerHTML = '<div style="text-align: center; color: #f44; padding: 20px;">Error loading configuration</div>';
    }
}

// Render a single config field based on its schema
export function renderConfigField(key, schema, value) {
    const description = schema.description ? 
        `<div class="config-field-description">${escapeHtml(schema.description)}</div>` : '';
    
    let control = '';
    
    switch (schema.control) {
        case 'select':
            control = renderSelectControl(key, schema, value);
            break;
        case 'slider':
            control = renderSliderControl(key, schema, value);
            break;
        case 'position_picker':
            control = renderPositionPicker(key, schema, value);
            break;
        case 'password':
            control = renderPasswordControl(key, schema, value);
            break;
        case 'text':
        default:
            control = renderTextControl(key, schema, value);
            break;
    }
    
    return `
        <div class="config-field">
            <label class="config-field-label">${escapeHtml(schema.label)}</label>
            ${description}
            ${control}
        </div>
    `;
}

// Render select (dropdown) control
export function renderSelectControl(key, schema, value) {
    const options = schema.options || [];
    const allowCustom = schema.allow_custom || false;
    
    // Check if current value matches any option
    const valueInOptions = options.some(opt => opt.value === value);
    const showCustom = allowCustom && value && !valueInOptions;
    
    let optionsHtml = options.map(opt => 
        `<option value="${escapeHtml(opt.value)}" ${opt.value === value ? 'selected' : ''}>${escapeHtml(opt.label)}</option>`
    ).join('');
    
    if (allowCustom) {
        optionsHtml += `<option value="__custom__" ${showCustom ? 'selected' : ''}>Custom...</option>`;
    }
    
    let html = `
        <div class="config-select-wrapper">
            <select id="config-${key}" data-original-value="${escapeHtml(value || '')}" 
                    ${allowCustom ? `onchange="toggleCustomInput('${key}')"` : ''}>
                ${optionsHtml}
            </select>
    `;
    
    if (allowCustom) {
        html += `
            <input type="text" 
                   id="config-${key}-custom" 
                   class="config-custom-input" 
                   placeholder="Enter custom value..."
                   value="${showCustom ? escapeHtml(value) : ''}"
                   style="display: ${showCustom ? 'block' : 'none'};"
            />
        `;
    }
    
    html += '</div>';
    return html;
}

// Toggle custom input visibility for select with allow_custom
export function toggleCustomInput(key) {
    const select = document.getElementById(`config-${key}`);
    const customInput = document.getElementById(`config-${key}-custom`);
    if (!select || !customInput) return;
    
    if (select.value === '__custom__') {
        customInput.style.display = 'block';
        customInput.focus();
    } else {
        customInput.style.display = 'none';
        customInput.value = '';
    }
}

// Render slider control
export function renderSliderControl(key, schema, value) {
    const min = schema.min !== undefined ? schema.min : 0;
    const max = schema.max !== undefined ? schema.max : 100;
    const step = schema.step !== undefined ? schema.step : 1;
    const numValue = parseFloat(value) || min;
    
    return `
        <div class="config-slider-container">
            <input type="range" 
                   id="config-${key}" 
                   class="config-slider"
                   min="${min}" 
                   max="${max}" 
                   step="${step}" 
                   value="${numValue}"
                   data-original-value="${value || ''}"
                   data-display-format="${schema.display_format || ''}"
                   oninput="updateSliderDisplay('${key}')"
            />
            <span class="config-slider-value" id="config-${key}-display">
                ${formatSliderValue(numValue, schema.display_format)}
            </span>
        </div>
    `;
}

// Format slider value for display
export function formatSliderValue(value, format) {
    switch (format) {
        case 'percent':
            return Math.round(value * 100) + '%';
        case 'percent_int':
            return Math.round(value) + '%';
        case 'seconds':
            return value + 's';
        default:
            return String(value);
    }
}

// Update slider display when value changes
export function updateSliderDisplay(key) {
    const slider = document.getElementById(`config-${key}`);
    const display = document.getElementById(`config-${key}-display`);
    if (!slider || !display) return;
    
    const format = slider.getAttribute('data-display-format') || '';
    display.textContent = formatSliderValue(parseFloat(slider.value), format);
}

// Initialize all sliders
export function initializeSliders() {
    document.querySelectorAll('.config-slider').forEach(slider => {
        const key = slider.id.replace('config-', '');
        updateSliderDisplay(key);
    });
}

// Render position picker (4 corners)
export function renderPositionPicker(key, schema, value) {
    const options = schema.options || [
        { value: 'top-left', label: 'TL' },
        { value: 'top-right', label: 'TR' },
        { value: 'bottom-left', label: 'BL' },
        { value: 'bottom-right', label: 'BR' }
    ];
    
    const optionsHtml = options.map(opt => `
        <div class="config-position-option ${opt.value === value ? 'selected' : ''}"
             data-value="${escapeHtml(opt.value)}"
             onclick="selectPosition('${key}', '${opt.value}')">
            ${escapeHtml(opt.label)}
        </div>
    `).join('');
    
    return `
        <input type="hidden" id="config-${key}" value="${escapeHtml(value || '')}" data-original-value="${escapeHtml(value || '')}" />
        <div class="config-position-picker">
            ${optionsHtml}
        </div>
    `;
}

// Select position in position picker
export function selectPosition(key, value) {
    const input = document.getElementById(`config-${key}`);
    if (!input) return;
    
    input.value = value;
    
    // Update visual selection
    const container = input.nextElementSibling;
    if (container) {
        container.querySelectorAll('.config-position-option').forEach(opt => {
            opt.classList.toggle('selected', opt.dataset.value === value);
        });
    }
}

// Render password control
export function renderPasswordControl(key, schema, value) {
    const hasValue = value && value.length > 0;
    return `
        <input type="password" 
               id="config-${key}" 
               value=""
               placeholder="${hasValue ? '•••••••• (leave blank to keep)' : 'Enter value'}"
               data-original-value="${escapeHtml(value || '')}"
               data-is-password="true"
        />
    `;
}

// Render text control
export function renderTextControl(key, schema, value) {
    const placeholder = schema.placeholder || '';
    return `
        <input type="text" 
               id="config-${key}" 
               value="${escapeHtml(value || '')}"
               placeholder="${escapeHtml(placeholder)}"
               data-original-value="${escapeHtml(value || '')}"
        />
    `;
}

// Save configuration to server
export async function saveConfiguration() {
    const container = document.getElementById('config-form-container');
    const messageDiv = document.getElementById('config-message');
    
    if (!container || !currentConfigData) return;
    
    const updates = [];
    
    // Iterate through schema to find all config fields
    for (const [key, schema] of Object.entries(currentConfigData.schema)) {
        const input = document.getElementById(`config-${key}`);
        if (!input) continue;
        
        let newValue = input.value;
        const originalValue = input.getAttribute('data-original-value') || '';
        const isPassword = input.getAttribute('data-is-password') === 'true';
        
        // Handle select with custom option
        if (schema.allow_custom && newValue === '__custom__') {
            const customInput = document.getElementById(`config-${key}-custom`);
            newValue = customInput ? customInput.value.trim() : '';
        }
        
        // For password fields, only update if a new value was entered
        if (isPassword && newValue === '') {
            continue;
        }
        
        // Trim string values
        if (typeof newValue === 'string') {
            newValue = newValue.trim();
        }
        
        if (newValue !== originalValue) {
            updates.push({ key, value: newValue });
        }
    }
    
    if (updates.length === 0) {
        showConfigMessage('No changes to save', 'info');
        return;
    }
    
    showConfigMessage('Saving...', 'info');
    
    try {
        for (const update of updates) {
            const response = await fetch('/api/config', {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({key: update.key, value: update.value})
            });
            
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to save configuration');
            }
        }
        
        showConfigMessage('Configuration saved successfully!', 'success');
        setTimeout(loadConfiguration, 1500);
    } catch (e) {
        console.error('Error saving configuration:', e);
        showConfigMessage('Error: ' + e.message, 'error');
    }
}

// Show config message
export function showConfigMessage(message, type) {
    const messageDiv = document.getElementById('config-message');
    if (messageDiv) {
        messageDiv.innerHTML = `<div class="config-message ${type}">${escapeHtml(message)}</div>`;
    }
}
