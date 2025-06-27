# GitHub & HACS Setup Guide

Your Sol integration is now ready for GitHub and HACS! Here's what you need to do:

## 📁 Repository Structure

```
Sol/
├── .github/
│   └── workflows/
│       └── validate.yml          # GitHub Actions validation
├── sol/                          # Integration directory
│   ├── __init__.py              # Main integration setup
│   ├── sensor.py                # Elevation and solstice sensors
│   ├── binary_sensor.py         # Binary elevation sensors
│   ├── helper.py                # Solar calculation utilities
│   ├── const.py                 # Constants and configuration
│   ├── manifest.json            # Integration metadata
│   └── README.md                # Detailed documentation
├── README.md                    # Repository README
├── info.md                      # HACS store description
├── hacs.json                    # HACS integration metadata
├── .gitignore                   # Git ignore rules
├── debug_elevation.py           # Debug script for elevation sensor
└── debug_binary_sensor.py       # Debug script for binary sensor
```

## 🚀 GitHub Setup Steps

### 1. Create GitHub Repository
1. Go to [GitHub](https://github.com) and create a new repository
2. Name it `sol-integration` (or your preferred name)
3. Make it public
4. Don't initialize with README (we already have one)

### 2. Update Repository URLs
Before pushing, update these files with your actual GitHub username:

**In `README.md`:**
- Replace `yourusername` with your actual GitHub username
- Update the repository URL in the badges

**In `sol/manifest.json`:**
- Replace `yourusername` with your actual GitHub username
- Update the documentation URL

**In `hacs.json`:**
- No changes needed (uses repository name)

### 3. Push to GitHub
```bash
git init
git add .
git commit -m "Initial commit: Sol integration for Home Assistant"
git branch -M main
git remote add origin https://github.com/yourusername/sol-integration.git
git push -u origin main
```

## 🔧 HACS Integration Setup

### 1. Add to HACS Store (Optional)
To make your integration available in the HACS store:

1. Fork the [HACS repository](https://github.com/hacs/default)
2. Add your integration to the appropriate category in `repositories.yaml`
3. Submit a pull request

### 2. Manual HACS Installation
Users can install your integration manually in HACS:

1. In HACS, go to "Integrations"
2. Click the three dots menu → "Custom repositories"
3. Add repository: `yourusername/sol-integration`
4. Category: "Integration"
5. Click "Add"
6. Search for "Sol" and install

## 📋 Pre-Release Checklist

- [ ] Update all `yourusername` references with your actual GitHub username
- [ ] Test the integration locally
- [ ] Verify all files are in the correct locations
- [ ] Check that `manifest.json` has correct metadata
- [ ] Ensure `hacs.json` is properly formatted
- [ ] Test the GitHub Actions workflow (will run on push)

## 🎯 Post-Release

### For Users
Users can install your integration by:

1. **HACS (Recommended):**
   - Add your repository as a custom repository
   - Search for "Sol" and install

2. **Manual:**
   - Download the `sol` folder
   - Place in `config/custom_components/`
   - Restart Home Assistant

### For You
- Monitor GitHub Issues for bug reports
- Update the integration as needed
- Consider adding to the HACS default store for wider distribution

## 🔍 Validation

The GitHub Actions workflow will automatically validate your integration structure when you push to GitHub. Check the "Actions" tab in your repository to see the validation results.

## 📝 Notes

- The integration is set up as a "Custom" integration (not in the default HACS store)
- Users will need to add it as a custom repository in HACS
- The debug scripts are included for troubleshooting but won't be installed with the integration
- All configuration is done via `configuration.yaml` (no config flow yet)

Your integration is now ready for the world! 🌟 