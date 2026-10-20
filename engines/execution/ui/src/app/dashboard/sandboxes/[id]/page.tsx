// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
import { parseDashboardUrl } from '@/lib/dashboard-url';
import SandboxDetailPage from './sandbox-detail';

export const dynamic = 'force-dynamic';

export default function Page() {
  const configuredUrl = process.env.AVENRA_UI_DATADOG_DASHBOARD_URL;
  const dashboardUrl = parseDashboardUrl(configuredUrl);
  if (configuredUrl?.trim() && !dashboardUrl) {
    console.warn(
      'AVENRA_UI_DATADOG_DASHBOARD_URL must be an HTTPS URL without credentials; hiding the dashboard link',
    );
  }
  return <SandboxDetailPage dashboardUrl={dashboardUrl} />;
}
