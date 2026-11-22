FROM manimcommunity/manim:v0.19.0

USER root
COPY runner.py /opt/runner/runner.py
RUN chmod 0555 /opt/runner/runner.py && mkdir -p /out && chown manimuser /out
# Non-root; filesystem is mounted read-only by compose except /out and /tmp (tmpfs).
USER manimuser
WORKDIR /tmp
EXPOSE 8000
ENTRYPOINT []
CMD ["python", "/opt/runner/runner.py"]
