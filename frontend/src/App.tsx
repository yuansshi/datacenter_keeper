import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Spinner } from "./components/ui";
import Activity from "./pages/Activity";
import Bookings from "./pages/Bookings";
import Dashboard from "./pages/Dashboard";
import DeviceDetail from "./pages/DeviceDetail";
import Devices from "./pages/Devices";
import ImportPage from "./pages/Import";
import Networks from "./pages/Networks";
import Racks from "./pages/Racks";
import Settings from "./pages/Settings";
import TestbedDetail from "./pages/TestbedDetail";
import Testbeds from "./pages/Testbeds";

// three.js is large: only load it when someone opens a floor plan
const FloorPlans = lazy(() => import("./pages/FloorPlans"));
const FloorPlanView = lazy(() => import("./pages/FloorPlanView"));

function Loading() {
  return (
    <div className="flex h-64 items-center justify-center text-muted">
      <Spinner />
    </div>
  );
}

export default function App() {
  return (
    <Layout>
      <Suspense fallback={<Loading />}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/devices" element={<Devices />} />
          <Route path="/devices/:id" element={<DeviceDetail />} />
          <Route path="/testbeds" element={<Testbeds />} />
          <Route path="/testbeds/:id" element={<TestbedDetail />} />
          <Route path="/bookings" element={<Bookings />} />
          <Route path="/networks" element={<Networks />} />
          <Route path="/racks" element={<Racks />} />
          <Route path="/racks/:id" element={<Racks />} />
          <Route path="/floor" element={<FloorPlans />} />
          <Route path="/floor/:id" element={<FloorPlanView />} />
          <Route path="/import" element={<ImportPage />} />
          <Route path="/activity" element={<Activity />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Dashboard />} />
        </Routes>
      </Suspense>
    </Layout>
  );
}
