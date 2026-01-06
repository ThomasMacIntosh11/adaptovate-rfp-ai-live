// frontend/src/api.js
import axios from "axios";

// Match your backend port (8000 by default; change if you ran uvicorn with --port 8001)
export const API_URL = "http://127.0.0.1:8000";

export const getRFPs = () => axios.get(`${API_URL}/rfps`);
export const refreshRFPs = () => axios.post(`${API_URL}/refresh`);