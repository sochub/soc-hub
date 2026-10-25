import { useEffect, useState } from 'react';
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { onAuthExpired, session } from '../../api/client';
import { saveReturnTo } from '../../features/auth/returnTo';

export default function ProtectedRoute() {
    const [isAuthenticated, setIsAuthenticated] = useState(() => session.active());
    const navigate = useNavigate();
    const location = useLocation();
    const here = location.pathname + location.search;

    useEffect(() => {
        return onAuthExpired(() => {
            saveReturnTo(here);
            setIsAuthenticated(false);
            navigate('/login', { replace: true });
        });
    }, [navigate, here]);

    useEffect(() => {
        if (!isAuthenticated) saveReturnTo(here);
    }, [isAuthenticated, here]);

    if (!isAuthenticated) {
        return <Navigate to="/login" replace />;
    }

    return <Outlet />;
}
