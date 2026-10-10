import { test, expect } from '@playwright/test';

test('home redirects to approvals and renders', async ({ page }) => {
  await page.goto('/');
  await expect(page).toHaveURL(/\/approvals/);
});
