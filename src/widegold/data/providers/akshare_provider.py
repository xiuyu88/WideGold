from __future__ import annotations

from widegold.data.indicators import dataframe_to_observations
from widegold.domain.enums import DataStatus
from widegold.schemas.indicators import IndicatorFetchRequest, IndicatorSeries


class AkshareIndicatorProvider:
    provider_name = "akshare"

    def fetch(self, request: IndicatorFetchRequest, spec: dict) -> IndicatorSeries:
        try:
            import akshare as ak
        except ImportError:
            return IndicatorSeries(
                indicator_id=request.indicator_id,
                source_id=spec.get("source_id", "AKSHARE"),
                status=DataStatus.UNAVAILABLE,
                warnings=["akshare package is not installed"],
            )

        params = spec.get("params", {})
        fn_name = params["function"]
        fn = getattr(ak, fn_name, None)
        if fn is None:
            return IndicatorSeries(
                indicator_id=request.indicator_id,
                source_id=spec.get("source_id", "AKSHARE"),
                status=DataStatus.UNAVAILABLE,
                warnings=[f"AKShare function {fn_name} not found"],
            )

        kwargs = dict(params.get("kwargs", {}))
        # A single logical indicator may be scoped to multiple indices while each upstream call
        # needs a different symbol. Keep that mapping in registry data rather than in business
        # code. Missing scope is a controlled unavailable result, not a provider exception.
        asset_params = params.get("asset_params", {})
        if request.asset_id is not None and asset_params:
            overrides = asset_params.get(request.asset_id)
            if overrides is None:
                return IndicatorSeries(
                    indicator_id=request.indicator_id,
                    source_id=spec.get("source_id", "AKSHARE"),
                    status=DataStatus.UNAVAILABLE,
                    warnings=[f"asset_not_supported:{request.asset_id}"],
                )
            kwargs.update(overrides)

        for key, value in list(kwargs.items()):
            if value == "$START_YYYYMMDD":
                kwargs[key] = request.start_date.strftime("%Y%m%d")
            elif value == "$END_YYYYMMDD":
                kwargs[key] = request.end_date.strftime("%Y%m%d")

        frame = fn(**kwargs)
        parser = spec["parser"]
        value_column = parser.get("value_column")
        # Some wide AKShare reports (for example CFTC commodity positioning) can add or
        # reorder commodity columns over time. Allow registry-driven semantic column matching
        # instead of hard-coding a fragile full column name in provider code.
        if value_column is None and parser.get("value_column_contains"):
            tokens = [str(token) for token in parser["value_column_contains"]]
            columns = list(getattr(frame, "columns", []))
            candidates = [
                str(column) for column in columns
                if all(token in str(column) for token in tokens)
            ]
            if len(candidates) == 1:
                value_column = candidates[0]
            elif not candidates:
                return IndicatorSeries(
                    indicator_id=request.indicator_id,
                    source_id=spec.get("source_id", "AKSHARE"),
                    status=DataStatus.UNAVAILABLE,
                    warnings=[f"AKShare {fn_name} could not resolve value column containing {tokens}"],
                )
            else:
                return IndicatorSeries(
                    indicator_id=request.indicator_id,
                    source_id=spec.get("source_id", "AKSHARE"),
                    status=DataStatus.UNAVAILABLE,
                    warnings=[f"AKShare {fn_name} value column match is ambiguous: {candidates}"],
                )
        if value_column is None:
            return IndicatorSeries(
                indicator_id=request.indicator_id,
                source_id=spec.get("source_id", "AKSHARE"),
                status=DataStatus.UNAVAILABLE,
                warnings=[f"AKShare {fn_name} parser has no value_column"],
            )
        rows = dataframe_to_observations(
            indicator_id=request.indicator_id,
            source_id=spec.get("source_id", "AKSHARE"),
            frame=frame,
            date_column=parser["date_column"],
            value_column=value_column,
            release_ts=request.as_of,
            definition_version=spec.get("definition_version", "1.0.0"),
            where=parser.get("where"),
            asset_id=request.asset_id,
        )
        rows = [row for row in rows if request.start_date <= row.observation_date <= request.end_date]
        status = DataStatus.VALID if rows else DataStatus.UNAVAILABLE
        return IndicatorSeries(
            indicator_id=request.indicator_id,
            source_id=spec.get("source_id", "AKSHARE"),
            observations=rows,
            status=status,
            warnings=[] if rows else [f"AKShare {fn_name} returned no usable observations"],
        )
