/** Absolute in-case paths. React Router v7 resolves *relative* links inside the splat route
 *  `/case/:caseId/*` against the full current URL, so relative links would keep appending
 *  segments (…/complaint/propagation/…). Always link with these helpers instead. */
export const casePath = (caseId: string, sub = 'overview') => `/case/${caseId}/${sub.replace(/^\/+/, '')}`
