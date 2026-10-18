import { useEffect, useState } from 'react';
import { Navigate, Outlet, useNavigate } from 'react-router-dom';
import { onAuthExpired, session } from '../../api/client';

export default function ProtectedRoute() {
    const [isAuthenticated, setIsAuthenticated] = useState(() => session.active());
    const navigate = useNavigate();

    useEffect(() => {
        return onAuthExpired(() => {
            setIsAuthenticated(false);
            navigate('/login', { replace: true });
        });
    }, [navigate]);

    if (!isAuthenticated) {
        return <Navigate to="/login" replace />;
    }

    return <Outlet />;
}
