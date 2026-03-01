import { useState, useCallback } from 'react';
import { getServicesHealth, ServiceStatus } from '../api';
import { usePolling } from '../hooks/usePolling';
import './ServiceHealth.css';

const REFRESH_MS = 10_000;

function dotClass(status: ServiceStatus['status']): string {
  if (status === 'ok')      return 'dot dot-ok';
  if (status === 'loading') return 'dot dot-loading';
  return 'dot dot-error';
}

function detailLabel(svc: ServiceStatus): string {
  if (typeof svc.detail === 'string') return svc.detail;
  const d = svc.detail as Record<string, unknown>;
  if (d.model)    return String(d.model);
  if (d.backends) return `${Object.keys(d.backends as object).length} backends`;
  return svc.status;
}

export function ServiceHealth() {
  const [services, setServices] = useState<ServiceStatus[] | null>(null);

  const poll = useCallback(async () => {
    try {
      const data = await getServicesHealth();
      setServices(data);
    } catch {
      // fail silently
    }
  }, []);

  usePolling(poll, REFRESH_MS);

  if (!services) return null;

  return (
    <div className="health-strip">
      {services.map(svc => (
        <span key={svc.name} className="health-pill" title={`${svc.name}: ${detailLabel(svc)}`}>
          <span className={dotClass(svc.status)} />
          {svc.name}
        </span>
      ))}
    </div>
  );
}
