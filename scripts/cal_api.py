#!/usr/bin/env python3
"""REST API for Cal.com booking operations. Agents call this mid-call.

Uses pg8000.dbapi (DBAPI 2.0) with %s params.
Runs on localhost:8099.
"""
import json, uuid, urllib.parse
from datetime import datetime, timedelta, timezone, date, time
from http.server import HTTPServer, BaseHTTPRequestHandler
import pg8000.dbapi

CAL_DB = dict(user="calcom", password="calcom2025", database="calcom", host="127.0.0.1", port=5433)
CAL_ADMIN_USER_ID = 1
CAL_ADMIN_EMAIL = "nextvisionorganization@gmail.com"
LISTEN_PORT = 8099


def _db():
    return pg8000.dbapi.connect(**CAL_DB)


class CalAPIHandler(BaseHTTPRequestHandler):

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str, ensure_ascii=False).encode())

    def _error(self, msg, status=400):
        self._json({"error": msg}, status)

    def do_GET(self):
        path = self.path.rstrip('/').split('?')[0]
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        if path == '/health':
            return self._json({"status": "ok", "db_host": CAL_DB["host"]})

        elif path == '/event-types':
            try:
                conn = _db(); cur = conn.cursor()
                cur.execute('SELECT id, title, slug, length, description FROM "EventType" WHERE "userId" = %s ORDER BY id', (CAL_ADMIN_USER_ID,))
                result = [{"id": r[0], "title": r[1], "slug": r[2], "length_minutes": r[3], "description": r[4]} for r in cur.fetchall()]
                cur.close(); conn.close()
                return self._json(result)
            except Exception as e:
                return self._error(str(e))

        elif path == '/slots':
            et_id = int(qs.get('event_type_id', ['1'])[0])
            date_str = qs.get('date', [''])[0]
            try:
                slots = _get_available_slots(et_id, date_str)
                return self._json({"slots": slots, "date": date_str})
            except Exception as e:
                return self._error(str(e))

        elif path == '/bookings':
            email = qs.get('email', [None])[0]
            phone = qs.get('phone', [None])[0]
            try:
                bookings = _get_bookings(email=email, phone=phone)
                return self._json({"bookings": bookings})
            except Exception as e:
                return self._error(str(e))

        return self._error("Not found", 404)

    def do_POST(self):
        path = self.path.rstrip('/')
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        data = json.loads(body) if body else {}

        if path == '/bookings':
            try:
                result = _create_booking(
                    event_type_id=data.get('event_type_id', 1),
                    start_time_str=data.get('start_time', ''),
                    attendee_name=data.get('name', ''),
                    attendee_email=data.get('email', ''),
                    attendee_phone=data.get('phone', ''),
                    title=data.get('title', '')
                )
                if 'error' in result:
                    return self._error(result['error'])
                return self._json(result, 201)
            except Exception as e:
                return self._error(str(e))

        if '/cancel' in path:
            uid = path.split('/')[2]
            try:
                _cancel_booking(uid)
                return self._json({"status": "cancelled"})
            except Exception as e:
                return self._error(str(e))

        if '/reschedule' in path:
            uid = path.split('/')[2]
            new_start = data.get('start_time', '')
            try:
                result = _reschedule_booking(uid, new_start)
                if 'error' in result:
                    return self._error(result['error'])
                return self._json(result)
            except Exception as e:
                return self._error(str(e))

        return self._error("Not found", 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def log_message(self, format, *args):
        pass


def _get_available_slots(event_type_id, date_str):
    conn = _db(); cur = conn.cursor()
    try:
        cur.execute('SELECT id FROM "Schedule" WHERE "userId" = %s LIMIT 1', (CAL_ADMIN_USER_ID,))
        sched = cur.fetchone()
        if not sched:
            return []
        schedule_id = sched[0]

        target_date = date.fromisoformat(date_str)
        dow = target_date.weekday()

        cur.execute('SELECT "startTime", "endTime" FROM "Availability" WHERE "scheduleId" = %s AND "userId" = %s AND %s = ANY(days) ORDER BY "startTime"', (schedule_id, CAL_ADMIN_USER_ID, dow))
        avail_rows = cur.fetchall()
        if not avail_rows:
            return []

        cur.execute('SELECT length FROM "EventType" WHERE id = %s', (event_type_id,))
        et_row = cur.fetchone()
        if not et_row:
            return []
        duration_min = et_row[0]

        pkt_tz = timezone(timedelta(hours=5))
        utc_tz = timezone.utc
        cur.execute('SELECT "startTime", "endTime" FROM "Booking" WHERE "eventTypeId" = %s AND status = %s AND "startTime" >= %s::timestamp AND "endTime" <= %s::timestamp',
                    (event_type_id, "accepted", f"{date_str}T00:00:00", f"{date_str}T23:59:59"))
        def _parse_ts(v):
            if isinstance(v, str):
                return datetime.fromisoformat(v).replace(tzinfo=utc_tz)
            return v.replace(tzinfo=utc_tz)
        booked = [(_parse_ts(r[0]), _parse_ts(r[1])) for r in cur.fetchall()]

        slots = []
        for start_t, end_t in avail_rows:
            start_str = str(start_t)
            end_str = str(end_t)
            if ':' in start_str:
                parts = start_str.split(':')
                start_tm = time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)
            elif isinstance(start_t, (datetime, date)):
                start_tm = start_t.time()
            else:
                start_tm = time(9, 0)
            if ':' in end_str:
                parts = end_str.split(':')
                end_tm = time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)
            elif isinstance(end_t, (datetime, date)):
                end_tm = end_t.time()
            else:
                end_tm = time(17, 0)
            cursor = datetime.combine(target_date, start_tm, pkt_tz)
            slot_end = datetime.combine(target_date, end_tm, pkt_tz)

            while cursor + timedelta(minutes=duration_min) <= slot_end:
                conflict = any(cursor < b_end and cursor + timedelta(minutes=duration_min) > b_start for b_start, b_end in booked)
                if not conflict:
                    slots.append(cursor.isoformat())
                cursor += timedelta(minutes=duration_min)
        return slots
    finally:
        cur.close(); conn.close()


def _create_booking(event_type_id, start_time_str, attendee_name, attendee_email, attendee_phone="", title=""):
    conn = _db(); cur = conn.cursor()
    try:
        booking_uid = str(uuid.uuid4())
        now_ts = datetime.now(timezone.utc)

        cur.execute('SELECT length, title FROM "EventType" WHERE id = %s', (event_type_id,))
        et = cur.fetchone()
        if not et:
            return {"error": "Event type not found"}
        duration_min = et[0]
        event_title = et[1] if not title else title

        start_dt = datetime.fromisoformat(start_time_str)
        end_dt = start_dt + timedelta(minutes=duration_min)

        response_json = json.dumps({"name": attendee_name, "email": attendee_email,
            "smsReminderNumber": attendee_phone, "location": {"value": "integrations:cal_video", "optionValue": ""},
            "guests": [], "customInputs": {}})

        cur.execute("""
            INSERT INTO "Booking" (uid, "eventTypeId", title, "userId", "startTime", "endTime",
                "createdAt", "updatedAt", location, paid, status, responses, metadata,
                "isRecorded", "iCalSequence", "iCalUID", "userPrimaryEmail")
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s)
            RETURNING id
        """, (booking_uid, event_type_id, event_title, CAL_ADMIN_USER_ID,
              start_dt, end_dt, now_ts, now_ts, "integrations:cal_video",
              False,               "accepted", response_json, "{}", False, 0,
              f"{booking_uid}@cal.com", CAL_ADMIN_EMAIL))
        booking_db_id = cur.fetchone()[0]
        conn.commit()

        cur.execute('INSERT INTO "Attendee" (email, name, "timeZone", locale, "bookingId", "phoneNumber") VALUES (%s, %s, %s, %s, %s, %s)',
                    (attendee_email, attendee_name, "Asia/Karachi", "en", booking_db_id, attendee_phone))
        conn.commit()

        ref_uid = str(uuid.uuid4())
        cur.execute('INSERT INTO "BookingReference" (type, uid, "bookingId", "meetingUrl") VALUES (%s, %s, %s, %s)',
                    ("cal_video", ref_uid, booking_db_id, f"https://cal.44-194-44-98.sslip.io/booking/{booking_uid}"))
        conn.commit()

        return {"id": booking_db_id, "uid": booking_uid, "title": event_title,
                "start_time": start_dt.isoformat(), "end_time": end_dt.isoformat(), "status": "accepted",
                "attendee_name": attendee_name, "attendee_email": attendee_email, "attendee_phone": attendee_phone}
    except Exception as e:
        import traceback
        return {"error": str(e), "traceback": traceback.format_exc()}
    finally:
        cur.close(); conn.close()


def _get_bookings(email=None, phone=None):
    conn = _db(); cur = conn.cursor()
    try:
        if email:
            cur.execute("""
                SELECT b.id, b.uid, b.title, b."startTime", b."endTime", b.status,
                       a.name, a.email, a."phoneNumber", b."createdAt"
                FROM "Booking" b JOIN "Attendee" a ON a."bookingId" = b.id
                WHERE a.email = %s AND b.status = %s
                ORDER BY b."startTime" DESC
            """,                 (email, "accepted"))
        elif phone:
            cur.execute("""
                SELECT b.id, b.uid, b.title, b."startTime", b."endTime", b.status,
                       a.name, a.email, a."phoneNumber", b."createdAt"
                FROM "Booking" b JOIN "Attendee" a ON a."bookingId" = b.id
                WHERE a."phoneNumber" = %s AND b.status = %s
                ORDER BY b."startTime" DESC
            """,                 (phone, "accepted"))
        else:
            return []
        return [{"id": r[0], "uid": r[1], "title": r[2], "start_time": str(r[3]),
                 "end_time": str(r[4]), "status": r[5], "attendee_name": r[6],
                 "attendee_email": r[7], "attendee_phone": r[8], "created_at": str(r[9])}
                for r in cur.fetchall()]
    finally:
        cur.close(); conn.close()


def _cancel_booking(booking_uid):
    conn = _db(); cur = conn.cursor()
    try:
        cur.execute('UPDATE "Booking" SET status = %s, "updatedAt" = %s WHERE uid = %s',
                     ("cancelled", datetime.now(timezone.utc), booking_uid))
        conn.commit()
    finally:
        cur.close(); conn.close()


def _reschedule_booking(booking_uid, new_start_time_str):
    conn = _db(); cur = conn.cursor()
    try:
        cur.execute('SELECT id, "eventTypeId" FROM "Booking" WHERE uid = %s', (booking_uid,))
        row = cur.fetchone()
        if not row:
            return {"error": "Booking not found"}
        booking_id, event_type_id = row

        cur.execute('SELECT length FROM "EventType" WHERE id = %s', (event_type_id,))
        duration_min = cur.fetchone()[0]

        new_start = datetime.fromisoformat(new_start_time_str)
        new_end = new_start + timedelta(minutes=duration_min)
        cur.execute('UPDATE "Booking" SET "startTime" = %s, "endTime" = %s, "updatedAt" = %s WHERE uid = %s',
                     (new_start, new_end, datetime.now(timezone.utc), booking_uid))
        conn.commit()
        return {"id": booking_id, "uid": booking_uid, "start_time": new_start.isoformat(),
                "end_time": new_end.isoformat(), "status": "accepted"}
    finally:
        cur.close(); conn.close()


def main():
    server = HTTPServer(('127.0.0.1', LISTEN_PORT), CalAPIHandler)
    print(f"Cal.com API on http://127.0.0.1:{LISTEN_PORT}")
    server.serve_forever()


if __name__ == '__main__':
    main()
