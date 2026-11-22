const assert = require('assert');
const path = require('path');
const fs = require('fs');
const { getConfig } = require('@expo/config');

const projectRoot = path.resolve(__dirname, '..');

const FORBIDDEN_SECRET_PATTERNS = [
  /DATABASE/i,
  /REDIS/i,
  /UPSTASH/i,
  /BREVO/i,
  /CLOUDINARY_API_SECRET/i,
  /R2_SECRET/i,
  /GEMINI/i,
  /SECRET/i,
  /PRIVATE/i,
  /SESSION_SECRET/i,
  /CSRF/i,
  /TOTP/i,
];

function checkVariant(variant) {
  process.env.APP_VARIANT = variant;
  const fullConfig = getConfig(projectRoot, { skipSDKVersionRequirement: false });
  const publicConfig = getConfig(projectRoot, {
    skipSDKVersionRequirement: false,
    isPublicConfig: true,
  });

  const exp = fullConfig.exp;
  const publicExp = publicConfig.exp;
  const suffix = variant === 'production' ? '' : `.${variant}`;
  const expectedId = `app.dormiostay.mobile${suffix}`;

  assert.strictEqual(
    exp.android?.package,
    expectedId,
    `Android package mismatch for ${variant}: expected ${expectedId}, got ${exp.android?.package}`,
  );
  assert.strictEqual(
    exp.ios?.bundleIdentifier,
    expectedId,
    `iOS bundleIdentifier mismatch for ${variant}: expected ${expectedId}, got ${exp.ios?.bundleIdentifier}`,
  );
  assert.strictEqual(
    exp.ios?.config?.usesNonExemptEncryption,
    false,
    `iOS usesNonExemptEncryption must be false`,
  );
  assert.deepStrictEqual(
    exp.android?.permissions,
    [],
    `Android permissions must be empty for T-04M foundation`,
  );
  assert(
    Array.isArray(exp.android?.blockedPermissions) && exp.android.blockedPermissions.length > 0,
    `Android blockedPermissions must be defined`,
  );

  // Check icon exists
  const iconPath = path.resolve(projectRoot, exp.icon);
  assert(fs.existsSync(iconPath), `Icon file not found: ${iconPath}`);

  // Check no forbidden secrets in public config
  const serialized = JSON.stringify(publicExp);
  for (const pattern of FORBIDDEN_SECRET_PATTERNS) {
    assert(
      !pattern.test(serialized),
      `Forbidden secret pattern ${pattern} found in exported Expo config for ${variant}`,
    );
  }

  console.log(`✓ Expo config verified for variant: ${variant} (${expectedId})`);
}

for (const variant of ['development', 'preview', 'production']) {
  checkVariant(variant);
}

// Verify eas.json build profiles and environment isolation
const easPath = path.resolve(projectRoot, 'eas.json');
assert(fs.existsSync(easPath), `eas.json not found: ${easPath}`);
const easConfig = JSON.parse(fs.readFileSync(easPath, 'utf8'));
assert(easConfig.build?.development, 'EAS development profile missing');
assert(easConfig.build?.preview, 'EAS preview profile missing');
assert(easConfig.build?.production, 'EAS production profile missing');

// Validate preview and production API base URLs in eas.json
const previewApi = easConfig.build.preview.env?.EXPO_PUBLIC_API_BASE_URL;
const prodApi = easConfig.build.production.env?.EXPO_PUBLIC_API_BASE_URL;
assert(
  previewApi && previewApi.startsWith('https://') && previewApi.endsWith('/api/v1'),
  `EAS preview API base must be an HTTPS URL ending in /api/v1 (got ${previewApi})`,
);
assert(
  prodApi && prodApi.startsWith('https://') && prodApi.endsWith('/api/v1'),
  `EAS production API base must be an HTTPS URL ending in /api/v1 (got ${prodApi})`,
);
assert(
  previewApi !== prodApi,
  'EAS preview and production must use distinct isolated API environments',
);

console.log('✓ EAS build profiles and environment isolation verified.');
console.log('All Expo configuration checks passed.');
